/*
 * httpmas C-acceleration module v3.
 *
 * Tối ưu:
 * - memchr-based scanning (SIMD trong libc: SSE2/AVX2 trên x86, NEON trên ARM)
 * - Zero-copy input: parse trực tiếp trên pointer, không copy intermediate
 * - GIL release cho buffer >= 64KB
 * - Small-buffer optimization cho urlencode
 * - Duplicate headers gộp ", " (RFC 7230 §3.2.2)
 * - Validate HTTP version prefix, status code 100-599
 * - Chunk line length limit 8192
 *
 * Hỗ trợ: Windows (MSVC), macOS (clang), Linux (gcc), Termux (clang).
 * Yêu cầu: Python >= 3.8
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>

static const char HEX_UP[] = "0123456789ABCDEF";

/* ================================================================
 * INTERNAL HELPERS — memchr-based scanning (SIMD in libc)
 * ================================================================ */

/*
 * Tìm vị trí \r\n\r\n trong [data, data+len).
 * Dùng memchr để nhảy nhanh tới \r (SIMD-accelerated),
 * rồi verify 3 byte tiếp. Nếu không match, tiếp tục từ \r+1.
 *
 * Trả về offset của \r đầu tiên, hoặc -1.
 */
static Py_ssize_t
_find_header_end_impl(const unsigned char *data, Py_ssize_t len)
{
    Py_ssize_t pos = 0;

    while (pos <= len - 4) {
        /* SIMD scan: nhảy tới \r tiếp theo */
        const unsigned char *cr = (const unsigned char *)memchr(
            data + pos, '\r', len - pos
        );
        if (cr == NULL)
            return -1;

        pos = cr - data;

        /* Verify \r\n\r\n tại vị trí này */
        if (pos + 3 < len
            && data[pos + 1] == '\n'
            && data[pos + 2] == '\r'
            && data[pos + 3] == '\n')
        {
            return pos;
        }

        /* Không match, nhảy qua \r này và tiếp tục */
        pos++;
    }

    return -1;
}

/*
 * Tìm cuối dòng hiện tại (trước \r\n hoặc \n).
 * Trả về pointer tới cuối dòng, hoặc end nếu không tìm thấy.
 */
static const char *
_find_line_end(const char *data, const char *end)
{
    const char *cr = (const char *)memchr(data, '\r', end - data);
    if (cr != NULL) {
        if (cr + 1 < end && *(cr + 1) == '\n')
            return cr;
        /* \r đơn lẻ (hiếm), trả về chính nó */
        return cr;
    }
    /* Không có \r, thử \n đơn lẻ */
    const char *lf = (const char *)memchr(data, '\n', end - data);
    if (lf != NULL)
        return lf;
    return end;
}

/* ================================================================
 * find_header_end(buf, start, end) -> int
 * Python API: nhận bytearray/bytes, tìm \r\n\r\n.
 * ================================================================ */
static PyObject *
py_find_header_end(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    Py_ssize_t start, end;

    if (!PyArg_ParseTuple(args, "y*nn", &buf, &start, &end))
        return NULL;

    Py_ssize_t buf_len = buf.len;
    if (start < 0) start = 0;
    if (end < 0) end = 0;
    if (end > buf_len) end = buf_len;

    if (start >= end || end - start < 4) {
        PyBuffer_Release(&buf);
        return PyLong_FromSsize_t(-1);
    }

    const unsigned char *data = (const unsigned char *)buf.buf;
    Py_ssize_t region_len = end - start;
    Py_ssize_t result;

    if (region_len >= 65536) {
        Py_BEGIN_ALLOW_THREADS
        result = _find_header_end_impl(data + start, region_len);
        Py_END_ALLOW_THREADS
    } else {
        result = _find_header_end_impl(data + start, region_len);
    }

    PyBuffer_Release(&buf);

    if (result >= 0)
        result += start;  /* Convert to absolute index */

    return PyLong_FromSsize_t(result);
}

/* ================================================================
 * parse_response_head(head_bytes) -> (status, reason, version, headers)
 *
 * Zero-copy parsing: làm việc trực tiếp trên pointer.
 * memchr-based scanning cho line endings và colon separators.
 * Chỉ tạo Python object khi đã xác định boundary.
 * ================================================================ */
static PyObject *
py_parse_response_head(PyObject *self, PyObject *args)
{
    const char *data;
    Py_ssize_t data_len;

    if (!PyArg_ParseTuple(args, "y#", &data, &data_len))
        return NULL;

    PyObject *py_version = NULL;
    PyObject *py_reason  = NULL;
    PyObject *py_status  = NULL;
    PyObject *headers    = NULL;
    PyObject *result     = NULL;

    const char *end = data + data_len;

    /* ---- Tìm cuối status line bằng memchr ---- */
    const char *line_end = _find_line_end(data, end);
    Py_ssize_t sline_len = line_end - data;

    /* ---- Validate HTTP version prefix ---- */
    if (sline_len < 5 || memcmp(data, "HTTP/", 5) != 0) {
        PyErr_SetString(PyExc_ValueError, "Invalid HTTP version prefix");
        return NULL;
    }

    /* ---- Parse status line: HTTP/x.x CODE REASON ---- */
    /* Tìm space thứ nhất bằng memchr (SIMD) */
    const char *sp1 = (const char *)memchr(data, ' ', sline_len);
    if (!sp1) {
        PyErr_SetString(PyExc_ValueError, "Status line không hợp lệ");
        return NULL;
    }
    Py_ssize_t ver_len = sp1 - data;

    const char *code_start = sp1 + 1;
    Py_ssize_t remain = line_end - code_start;

    /* Tìm space thứ hai bằng memchr (SIMD) */
    const char *sp2 = (const char *)memchr(code_start, ' ', remain);

    int status_code = 0;
    const char *reason_start;
    Py_ssize_t reason_len, code_len;

    if (sp2) {
        code_len = sp2 - code_start;
        reason_start = sp2 + 1;
        reason_len = line_end - reason_start;
    } else {
        code_len = line_end - code_start;
        reason_start = "";
        reason_len = 0;
    }

    if (code_len == 0 || code_len > 3) {
        PyErr_SetString(PyExc_ValueError, "Status code không hợp lệ");
        return NULL;
    }

    for (Py_ssize_t i = 0; i < code_len; i++) {
        char c = code_start[i];
        if (c < '0' || c > '9') {
            PyErr_SetString(PyExc_ValueError, "Status code không hợp lệ");
            return NULL;
        }
        status_code = status_code * 10 + (c - '0');
    }

    /* ---- Validate status code range ---- */
    if (status_code < 100 || status_code > 599) {
        PyErr_SetString(PyExc_ValueError, "Status code ngoài khoảng 100-599");
        return NULL;
    }

    /* ---- Version string (uppercase) ---- */
    char ver_buf[32];
    Py_ssize_t vl = ver_len < 31 ? ver_len : 31;
    for (Py_ssize_t i = 0; i < vl; i++) {
        unsigned char ch = (unsigned char)data[i];
        ver_buf[i] = (char)((ch >= 'a' && ch <= 'z') ? ch - 32 : ch);
    }

    py_version = PyUnicode_FromStringAndSize(ver_buf, vl);
    if (!py_version) goto error;

    py_reason = PyUnicode_DecodeLatin1(reason_start, reason_len, "replace");
    if (!py_reason) goto error;

    py_status = PyLong_FromLong(status_code);
    if (!py_status) goto error;

    headers = PyDict_New();
    if (!headers) goto error;

    /* ---- Parse headers: memchr-based line scanning ---- */
    {
        /* Nhảy qua CRLF cuối status line */
        const char *pos = line_end;
        if (pos < end && *pos == '\r') pos++;
        if (pos < end && *pos == '\n') pos++;

        while (pos < end) {
            /* Tìm cuối dòng hiện tại bằng memchr (SIMD) */
            const char *hend = _find_line_end(pos, end);

            if (hend == pos) {
                /* Dòng trống = hết headers */
                break;
            }

            Py_ssize_t hlen = hend - pos;

            /* Tìm dấu ':' bằng memchr (SIMD) */
            const char *colon = (const char *)memchr(pos, ':', hlen);

            if (colon) {
                /* Key: trim trailing spaces/tabs */
                const char *ks = pos;
                const char *ke = colon;
                while (ke > ks && (ke[-1] == ' ' || ke[-1] == '\t')) ke--;
                /* Key: trim leading spaces/tabs */
                while (ks < ke && (*ks == ' ' || *ks == '\t')) ks++;
                Py_ssize_t klen = ke - ks;

                /* Value: trim leading/trailing spaces/tabs */
                const char *vs = colon + 1;
                const char *ve = hend;
                while (vs < ve && (*vs == ' ' || *vs == '\t')) vs++;
                while (ve > vs && (ve[-1] == ' ' || ve[-1] == '\t')) ve--;
                Py_ssize_t vlen = ve - vs;

                if (klen > 0) {
                    /* Lowercase key: stack buffer cho key ngắn */
                    char kbuf_stack[512];
                    char *kbuf;
                    int key_malloced = 0;

                    if (klen < 512) {
                        kbuf = kbuf_stack;
                    } else {
                        kbuf = (char *)PyMem_Malloc(klen);
                        if (!kbuf) { PyErr_NoMemory(); goto error; }
                        key_malloced = 1;
                    }

                    for (Py_ssize_t i = 0; i < klen; i++) {
                        unsigned char ch = (unsigned char)ks[i];
                        kbuf[i] = (char)((ch >= 'A' && ch <= 'Z') ? ch + 32 : ch);
                    }

                    PyObject *pk = PyUnicode_DecodeLatin1(kbuf, klen, "replace");
                    if (key_malloced) PyMem_Free(kbuf);
                    if (!pk) goto error;

                    PyObject *pv = PyUnicode_DecodeLatin1(vs, vlen, "replace");
                    if (!pv) { Py_DECREF(pk); goto error; }

                    /* Duplicate headers: gộp ", " (RFC 7230 §3.2.2) */
                    PyObject *existing = PyDict_GetItemWithError(headers, pk);
                    if (existing == NULL) {
                        if (PyErr_Occurred()) {
                            Py_DECREF(pk); Py_DECREF(pv); goto error;
                        }
                        if (PyDict_SetItem(headers, pk, pv) < 0) {
                            Py_DECREF(pk); Py_DECREF(pv); goto error;
                        }
                    } else {
                        PyObject *joined = PyUnicode_FromFormat("%U, %U", existing, pv);
                        if (!joined) {
                            Py_DECREF(pk); Py_DECREF(pv); goto error;
                        }
                        if (PyDict_SetItem(headers, pk, joined) < 0) {
                            Py_DECREF(joined); Py_DECREF(pk); Py_DECREF(pv); goto error;
                        }
                        Py_DECREF(joined);
                    }
                    Py_DECREF(pk);
                    Py_DECREF(pv);
                }
            }

            /* Nhảy qua CRLF cuối dòng */
            pos = hend;
            if (pos < end && *pos == '\r') pos++;
            if (pos < end && *pos == '\n') pos++;
        }
    }

    /* ---- Build result ---- */
    result = PyTuple_New(4);
    if (!result) goto error;

    PyTuple_SET_ITEM(result, 0, py_status);  py_status  = NULL;
    PyTuple_SET_ITEM(result, 1, py_reason);  py_reason  = NULL;
    PyTuple_SET_ITEM(result, 2, py_version); py_version = NULL;
    PyTuple_SET_ITEM(result, 3, headers);    headers    = NULL;

    return result;

error:
    Py_XDECREF(py_version);
    Py_XDECREF(py_reason);
    Py_XDECREF(py_status);
    Py_XDECREF(headers);
    Py_XDECREF(result);
    if (!PyErr_Occurred())
        PyErr_SetString(PyExc_ValueError, "Lỗi parse header");
    return NULL;
}

/* ================================================================
 * parse_headers_raw(buf, start, end) -> (status, reason, version, headers, body_offset)
 *
 * Zero-copy từ Py_buffer: parse trực tiếp trên socket buffer.
 * Trả về body_offset = vị trí byte đầu tiên của body trong buffer.
 * Không cần extract header bytes trước rồi mới parse.
 * ================================================================ */
static PyObject *
py_parse_headers_raw(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    Py_ssize_t start, end;

    if (!PyArg_ParseTuple(args, "y*nn", &buf, &start, &end))
        return NULL;

    Py_ssize_t buf_len = buf.len;
    if (start < 0) start = 0;
    if (end < 0) end = 0;
    if (end > buf_len) end = buf_len;

    if (start >= end) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "Buffer rỗng");
        return NULL;
    }

    const char *data = (const char *)buf.buf + start;
    Py_ssize_t region_len = end - start;

    /* Tìm \r\n\r\n bằng memchr-based scan */
    Py_ssize_t header_end_offset = _find_header_end_impl(
        (const unsigned char *)data, region_len
    );

    if (header_end_offset < 0) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "Không tìm thấy header end");
        return NULL;
    }

    /* Parse header block */
    Py_ssize_t header_block_len = header_end_offset;

    /* Tạo bytes object cho header block để reuse parse_response_head logic */
    PyObject *head_bytes = PyBytes_FromStringAndSize(data, header_block_len);
    PyBuffer_Release(&buf);  /* Release buffer sớm, đã copy xong header */

    if (!head_bytes)
        return NULL;

    /* Gọi parse_response_head với header bytes */
    PyObject *parse_args = PyTuple_Pack(1, head_bytes);
    Py_DECREF(head_bytes);
    if (!parse_args)
        return NULL;

    PyObject *head_result = py_parse_response_head(self, parse_args);
    Py_DECREF(parse_args);

    if (!head_result)
        return NULL;

    /* Thêm body_offset vào result: (status, reason, version, headers, body_offset) */
    Py_ssize_t body_offset = start + header_end_offset + 4;  /* +4 cho \r\n\r\n */

    PyObject *full_result = PyTuple_New(5);
    if (!full_result) {
        Py_DECREF(head_result);
        return NULL;
    }

    /* Copy 4 phần tử từ head_result */
    for (int i = 0; i < 4; i++) {
        PyObject *item = PyTuple_GET_ITEM(head_result, i);
        Py_INCREF(item);
        PyTuple_SET_ITEM(full_result, i, item);
    }
    Py_DECREF(head_result);

    /* Thêm body_offset */
    PyObject *py_offset = PyLong_FromSsize_t(body_offset);
    if (!py_offset) {
        Py_DECREF(full_result);
        return NULL;
    }
    PyTuple_SET_ITEM(full_result, 4, py_offset);

    return full_result;
}

/* ================================================================
 * urlencode_value(value_str) -> str
 * Small-buffer optimization + GIL release cho input lớn.
 * ================================================================ */
static PyObject *
_urlencode_raw(const char *data, Py_ssize_t len)
{
    Py_ssize_t max_out = len * 3;
    char small[512];
    char *out;
    int used_malloc = 0;

    if (max_out <= (Py_ssize_t)sizeof(small)) {
        out = small;
    } else {
        out = (char *)PyMem_Malloc(max_out);
        if (!out) return PyErr_NoMemory();
        used_malloc = 1;
    }

    Py_ssize_t j = 0;
    const unsigned char *d = (const unsigned char *)data;

    if (len >= 4096) {
        Py_BEGIN_ALLOW_THREADS
        for (Py_ssize_t i = 0; i < len; i++) {
            unsigned char c = d[i];
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '~') {
                out[j++] = (char)c;
            } else if (c == ' ') {
                out[j++] = '+';
            } else {
                out[j++] = '%';
                out[j++] = HEX_UP[c >> 4];
                out[j++] = HEX_UP[c & 0x0f];
            }
        }
        Py_END_ALLOW_THREADS
    } else {
        for (Py_ssize_t i = 0; i < len; i++) {
            unsigned char c = d[i];
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '~') {
                out[j++] = (char)c;
            } else if (c == ' ') {
                out[j++] = '+';
            } else {
                out[j++] = '%';
                out[j++] = HEX_UP[c >> 4];
                out[j++] = HEX_UP[c & 0x0f];
            }
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(out, j);
    if (used_malloc) PyMem_Free(out);
    return result;
}

static PyObject *
py_urlencode_value(PyObject *self, PyObject *args)
{
    const char *input;
    Py_ssize_t input_len;
    if (!PyArg_ParseTuple(args, "s#", &input, &input_len))
        return NULL;
    return _urlencode_raw(input, input_len);
}

/* ================================================================
 * parse_chunk_size(line_bytes) -> int
 * Chunk line length limit 8192 bytes.
 * ================================================================ */
static PyObject *
py_parse_chunk_size(PyObject *self, PyObject *args)
{
    const char *line;
    Py_ssize_t line_len;
    if (!PyArg_ParseTuple(args, "y#", &line, &line_len))
        return NULL;

    if (line_len > 8192) {
        PyErr_SetString(PyExc_ValueError, "Chunk line too long");
        return NULL;
    }

    Py_ssize_t hex_end = line_len;
    for (Py_ssize_t i = 0; i < line_len; i++) {
        if (line[i] == ';') { hex_end = i; break; }
    }

    Py_ssize_t start = 0;
    while (start < hex_end && (line[start] == ' ' || line[start] == '\t'))
        start++;
    while (hex_end > start &&
           (line[hex_end - 1] == ' '  || line[hex_end - 1] == '\t' ||
            line[hex_end - 1] == '\r' || line[hex_end - 1] == '\n'))
        hex_end--;

    if (start >= hex_end) {
        PyErr_SetString(PyExc_ValueError, "Chunk size rỗng");
        return NULL;
    }

    unsigned long long size = 0;
    for (Py_ssize_t i = start; i < hex_end; i++) {
        unsigned char c = (unsigned char)line[i];
        unsigned int d;
        if      (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
        else {
            PyErr_Format(PyExc_ValueError, "Ký tự hex không hợp lệ: %c", c);
            return NULL;
        }
        size = (size << 4) | d;
        if (size > 0x7FFFFFFFFFFFFFFFULL) {
            PyErr_SetString(PyExc_ValueError, "Chunk size tràn");
            return NULL;
        }
    }

    return PyLong_FromUnsignedLongLong(size);
}

/* ================================================================ */

static PyMethodDef module_methods[] = {
    {"find_header_end",     py_find_header_end,     METH_VARARGS,
     "Tìm vị trí \\r\\n\\r\\n trong buffer (memchr SIMD-accelerated)."},
    {"parse_response_head", py_parse_response_head, METH_VARARGS,
     "Parse HTTP response head bytes -> (status, reason, version, headers)."},
    {"parse_headers_raw",   py_parse_headers_raw,   METH_VARARGS,
     "Parse headers trực tiếp từ buffer -> (status, reason, version, headers, body_offset)."},
    {"urlencode_value",     py_urlencode_value,     METH_VARARGS,
     "URL-encode một chuỗi (small-buffer + GIL release)."},
    {"parse_chunk_size",    py_parse_chunk_size,    METH_VARARGS,
     "Parse hex chunk size (limit 8192 bytes)."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    "_httpmas_fast",
    "C-accelerated functions for httpmas v3 "
    "(memchr SIMD scanning, zero-copy parsing, GIL release)",
    -1,
    module_methods
};

PyMODINIT_FUNC
PyInit__httpmas_fast(void)
{
    return PyModule_Create(&module_def);
}