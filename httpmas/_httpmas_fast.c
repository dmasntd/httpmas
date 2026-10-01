#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <string.h>

static const char HEX_UP[] = "0123456789ABCDEF";

static Py_ssize_t
_find_header_end_impl(const unsigned char *data, Py_ssize_t len)
{
    Py_ssize_t pos = 0;

    while (pos <= len - 4) {
        const unsigned char *cr = (const unsigned char *)memchr(
            data + pos, '\r', len - pos
        );
        if (cr == NULL)
            return -1;

        pos = cr - data;

        if (pos + 3 < len
            && data[pos + 1] == '\n'
            && data[pos + 2] == '\r'
            && data[pos + 3] == '\n')
        {
            return pos;
        }

        pos++;
    }

    return -1;
}

static const char *
_find_line_end(const char *data, const char *end)
{
    const char *cr = (const char *)memchr(data, '\r', end - data);
    if (cr != NULL) {
        if (cr + 1 < end && *(cr + 1) == '\n')
            return cr;
        return cr;
    }
    const char *lf = (const char *)memchr(data, '\n', end - data);
    if (lf != NULL)
        return lf;
    return end;
}

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
        result += start;

    return PyLong_FromSsize_t(result);
}

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
    const char *line_end = _find_line_end(data, end);
    Py_ssize_t sline_len = line_end - data;

    if (sline_len < 5 || memcmp(data, "HTTP/", 5) != 0) {
        PyErr_SetString(PyExc_ValueError, "Invalid HTTP version prefix");
        return NULL;
    }

    const char *sp1 = (const char *)memchr(data, ' ', sline_len);
    if (!sp1) {
        PyErr_SetString(PyExc_ValueError, "Status line không hợp lệ");
        return NULL;
    }
    Py_ssize_t ver_len = sp1 - data;

    const char *code_start = sp1 + 1;
    Py_ssize_t remain = line_end - code_start;

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

    if (status_code < 100 || status_code > 599) {
        PyErr_SetString(PyExc_ValueError, "Status code ngoài khoảng 100-599");
        return NULL;
    }

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

    {
        const char *pos = line_end;
        if (pos < end && *pos == '\r') pos++;
        if (pos < end && *pos == '\n') pos++;

        while (pos < end) {
            const char *hend = _find_line_end(pos, end);

            if (hend == pos) {
                break;
            }

            Py_ssize_t hlen = hend - pos;

            const char *colon = (const char *)memchr(pos, ':', hlen);

            if (colon) {
                const char *ks = pos;
                const char *ke = colon;
                while (ke > ks && (ke[-1] == ' ' || ke[-1] == '\t')) ke--;
                while (ks < ke && (*ks == ' ' || *ks == '\t')) ks++;
                Py_ssize_t klen = ke - ks;

                const char *vs = colon + 1;
                const char *ve = hend;
                while (vs < ve && (*vs == ' ' || *vs == '\t')) vs++;
                while (ve > vs && (ve[-1] == ' ' || ve[-1] == '\t')) ve--;
                Py_ssize_t vlen = ve - vs;

                if (klen > 0) {
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

            pos = hend;
            if (pos < end && *pos == '\r') pos++;
            if (pos < end && *pos == '\n') pos++;
        }
    }

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

    Py_ssize_t header_end_offset = _find_header_end_impl(
        (const unsigned char *)data, region_len
    );

    if (header_end_offset < 0) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "Không tìm thấy header end");
        return NULL;
    }

    Py_ssize_t header_block_len = header_end_offset;
    PyObject *head_bytes = PyBytes_FromStringAndSize(data, header_block_len);
    PyBuffer_Release(&buf);

    if (!head_bytes)
        return NULL;

    PyObject *parse_args = PyTuple_Pack(1, head_bytes);
    Py_DECREF(head_bytes);
    if (!parse_args)
        return NULL;

    PyObject *head_result = py_parse_response_head(self, parse_args);
    Py_DECREF(parse_args);

    if (!head_result)
        return NULL;

    Py_ssize_t body_offset = start + header_end_offset + 4;

    PyObject *full_result = PyTuple_New(5);
    if (!full_result) {
        Py_DECREF(head_result);
        return NULL;
    }

    for (int i = 0; i < 4; i++) {
        PyObject *item = PyTuple_GET_ITEM(head_result, i);
        Py_INCREF(item);
        PyTuple_SET_ITEM(full_result, i, item);
    }
    Py_DECREF(head_result);

    PyObject *py_offset = PyLong_FromSsize_t(body_offset);
    if (!py_offset) {
        Py_DECREF(full_result);
        return NULL;
    }
    PyTuple_SET_ITEM(full_result, 4, py_offset);

    return full_result;
}

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
