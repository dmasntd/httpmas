"""
Giao diện dòng lệnh (CLI) cho httpmas.
Banner cyberpunk với logo ASCII gradient,
khung bảng neon căn chỉnh chính xác, màu viền đồng bộ 4 góc.
"""

import os
import re
import sys
import time
import shutil
import platform

from .version import __version__


# ============================================================
# TERMINAL COLOR - PALETTE NEON RỰC RỠ
# ============================================================
class TerminalColor:
    """Quản lý mã màu RGB TrueColor với palette neon rực rỡ."""

    # === Escape codes cơ bản ===
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    # === NEON (saturation 100%) ===
    NEON_CYAN = "\033[38;2;0;255;255m"
    NEON_MAGENTA = "\033[38;2;255;0;255m"
    NEON_PINK = "\033[38;2;255;20;147m"
    NEON_YELLOW = "\033[38;2;255;255;0m"
    NEON_GREEN = "\033[38;2;57;255;20m"
    NEON_ORANGE = "\033[38;2;255;102;0m"
    NEON_RED = "\033[38;2;255;0;60m"

    # === ELECTRIC ===
    ELECTRIC_BLUE = "\033[38;2;0;191;255m"
    ELECTRIC_PURPLE = "\033[38;2;191;0;255m"
    ELECTRIC_VIOLET = "\033[38;2;138;43;226m"
    ELECTRIC_LIME = "\033[38;2;204;255;0m"
    ELECTRIC_TEAL = "\033[38;2;0;255;204m"

    # === PASTEL ===
    PASTEL_CYAN = "\033[38;2;135;206;235m"
    PASTEL_PINK = "\033[38;2;255;182;193m"
    PASTEL_LAVENDER = "\033[38;2;186;135;255m"
    PASTEL_MINT = "\033[38;2;152;255;152m"
    PASTEL_GOLD = "\033[38;2;255;215;0m"

    # === NEUTRAL ===
    NEUTRAL_WHITE = "\033[38;2;250;250;255m"
    NEUTRAL_SILVER = "\033[38;2;192;192;210m"
    NEUTRAL_GRAY = "\033[38;2;110;110;130m"

    @staticmethod
    def rgb(r: int, g: int, b: int) -> str:
        """Tạo mã màu RGB ANSI tùy chỉnh."""
        return f"\033[38;2;{r};{g};{b}m"

    @staticmethod
    def bg_rgb(r: int, g: int, b: int) -> str:
        """Tạo mã màu nền RGB ANSI."""
        return f"\033[48;2;{r};{g};{b}m"

    @staticmethod
    def visible_len(text: str) -> int:
        """Tính độ dài hiển thị thực (bỏ qua mã ANSI)."""
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        return len(clean)

    @classmethod
    def pad(cls, text: str, width: int) -> str:
        """Đệm chuỗi (có thể chứa ANSI) đến độ rộng hiển thị."""
        visible = cls.visible_len(text)
        return text + " " * max(0, width - visible)

    @classmethod
    def truncate(cls, text: str, width: int) -> str:
        """Cắt chuỗi theo độ rộng hiển thị, an toàn với mã ANSI."""
        if cls.visible_len(text) <= width:
            return text

        out = []
        vis = 0
        i = 0
        while i < len(text):
            if text[i] == "\033":
                j = text.find("m", i)
                if j == -1:
                    break
                out.append(text[i:j + 1])
                i = j + 1
                continue
            if vis >= width:
                break
            out.append(text[i])
            vis += 1
            i += 1
        out.append(cls.RESET)
        return "".join(out)

    @classmethod
    def gradient_text(cls, text: str, colors: list) -> str:
        """Tô gradient từng ký tự bằng nội suy tuyến tính."""
        if not text or not colors:
            return text

        result = []
        n = len(text)
        m = len(colors)

        for i, char in enumerate(text):
            t = i / max(n - 1, 1)
            idx = t * (m - 1)
            idx_low = int(idx)
            idx_high = min(idx_low + 1, m - 1)
            frac = idx - idx_low

            r1, g1, b1 = colors[idx_low]
            r2, g2, b2 = colors[idx_high]
            r = int(r1 + (r2 - r1) * frac)
            g = int(g1 + (g2 - g1) * frac)
            b = int(b1 + (b2 - b1) * frac)

            result.append(cls.rgb(r, g, b) + char)

        return "".join(result) + cls.RESET


# ============================================================
# TABLE BUILDER - KHUNG NEON CĂN CHỈNH + ĐỒNG BỘ MÀU 4 GÓC
# ============================================================
class TableBuilder:
    """Xây dựng khung bảng neon chính xác và đồng bộ màu.

    Nguyên tắc đồng bộ màu:
    - Viền ngang: gradient trái → phải.
    - Viền dọc TRÁI = màu ĐẦU gradient (khớp góc trái).
    - Viền dọc PHẢI = màu CUỐI gradient (khớp góc phải).
    → 4 góc khép kín cùng màu, không bị chỏi.
    """

    DOUBLE = {
        "tl": "╔", "tr": "╗", "bl": "╚", "br": "╝",
        "h": "═", "v": "║", "lt": "╠", "rt": "╣",
    }
    SINGLE = {
        "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
        "h": "─", "v": "│", "lt": "├", "rt": "┤",
    }

    @staticmethod
    def auto_width(max_width: int = 66) -> int:
        """Tính độ rộng khung theo kích thước terminal."""
        cols = shutil.get_terminal_size((80, 24)).columns
        return max(40, min(max_width, cols - 2))

    def __init__(
        self,
        width: int = None,
        border_color: str = None,
        text_color: str = None,
        style: str = "double",
        gradient: list = None,
    ) -> None:
        """Khởi tạo TableBuilder.

        Tham số:
            width: Độ rộng tổng (None = tự động theo terminal).
            border_color: Màu viền khi không có gradient.
            text_color: Màu chữ mặc định.
            style: "double" hoặc "single".
            gradient: Dải màu (danh sách tuple RGB) cho toàn khung.
        """
        self.width = width if width else self.auto_width()
        self.border_color = border_color or TerminalColor.NEON_CYAN
        self.text_color = text_color or TerminalColor.NEUTRAL_WHITE
        self.chars = self.DOUBLE if style == "double" else self.SINGLE
        self.gradient = gradient

        # === Đồng bộ màu viền dọc với 2 đầu gradient ngang ===
        if gradient:
            self._left_color = TerminalColor.rgb(*gradient[0])
            self._right_color = TerminalColor.rgb(*gradient[-1])
        else:
            self._left_color = self.border_color
            self._right_color = self.border_color

        # inner_width: số ký tự giữa 2 viền dọc
        self.inner_width = self.width - 2
        # content_width: vùng nội dung thực (trừ 2 space đệm)
        self.content_width = self.inner_width - 2

    def _c(self, color: str, char: str) -> str:
        """Tô màu một ký tự viền."""
        return f"{color}{char}{TerminalColor.RESET}"

    def _left(self) -> str:
        """Lề + viền trái (màu đầu gradient) + space đệm."""
        return "  " + self._c(self._left_color, self.chars["v"]) + " "

    def _right(self) -> str:
        """Space đệm + viền phải (màu cuối gradient)."""
        return " " + self._c(self._right_color, self.chars["v"])

    def top_border(self, gradient: list = None) -> str:
        """Viền trên với gradient."""
        g = gradient or self.gradient
        text = (
            self.chars["tl"]
            + self.chars["h"] * self.inner_width
            + self.chars["tr"]
        )
        if g:
            return "  " + TerminalColor.gradient_text(text, g)
        return "  " + self._c(self.border_color, text)

    def bottom_border(self, gradient: list = None) -> str:
        """Viền dưới với gradient."""
        g = gradient or self.gradient
        text = (
            self.chars["bl"]
            + self.chars["h"] * self.inner_width
            + self.chars["br"]
        )
        if g:
            return "  " + TerminalColor.gradient_text(text, g)
        return "  " + self._c(self.border_color, text)

    def separator(self, gradient: list = None) -> str:
        """Đường phân cách ngang với gradient."""
        g = gradient or self.gradient
        text = (
            self.chars["lt"]
            + self.chars["h"] * self.inner_width
            + self.chars["rt"]
        )
        if g:
            return "  " + TerminalColor.gradient_text(text, g)
        return "  " + self._c(self.border_color, text)

    def row(self, content: str, content_color: str = None) -> str:
        """Một dòng nội dung căn trái, đệm đúng độ rộng."""
        color = content_color or self.text_color
        content = TerminalColor.truncate(content, self.content_width)
        colored = f"{color}{content}{TerminalColor.RESET}"
        padded = TerminalColor.pad(colored, self.content_width)
        return self._left() + padded + self._right()

    def centered_row(self, content: str, content_color: str = None) -> str:
        """Một dòng nội dung căn giữa."""
        color = content_color or self.text_color
        content = TerminalColor.truncate(content, self.content_width)
        colored = f"{color}{content}{TerminalColor.RESET}"
        visible = TerminalColor.visible_len(colored)
        total_pad = self.content_width - visible
        left_pad = total_pad // 2
        right_pad = total_pad - left_pad
        return (
            self._left()
            + " " * left_pad
            + colored
            + " " * right_pad
            + self._right()
        )

    def kv_row(
        self,
        label: str,
        value: str,
        label_color: str = None,
        value_color: str = None,
    ) -> str:
        """Dòng key-value với separator, căn chỉnh chính xác."""
        lc = (label_color if label_color is not None
              else TerminalColor.PASTEL_CYAN)
        vc = (value_color if value_color is not None
              else TerminalColor.NEON_YELLOW)

        sep_w = 3
        label_w = int(self.content_width * 0.35)
        value_w = self.content_width - label_w - sep_w

        label_part = TerminalColor.pad(
            lc + TerminalColor.truncate(str(label), label_w)
            + TerminalColor.RESET, label_w
        )
        value_part = TerminalColor.pad(
            vc + TerminalColor.truncate(str(value), value_w)
            + TerminalColor.RESET, value_w
        )
        sep_part = (
            f"{TerminalColor.NEUTRAL_GRAY}│{TerminalColor.RESET}"
        )

        content = f"{label_part} {sep_part} {value_part}"
        return self._left() + content + self._right()

    def build(
        self,
        rows: list,
        title: str = None,
        title_color: str = None,
        gradient: list = None,
    ) -> str:
        """Xây dựng bảng hoàn chỉnh."""
        g = gradient or self.gradient
        lines = [self.top_border(g)]

        if title:
            tc = title_color or TerminalColor.NEON_CYAN
            lines.append(
                self.centered_row(
                    f"{TerminalColor.BOLD}{tc}{title}"
                    f"{TerminalColor.RESET}"
                )
            )
            lines.append(self.separator(g))

        lines.extend(rows)
        lines.append(self.bottom_border(g))
        return "\n".join(lines)


# ============================================================
# CLI BANNER
# ============================================================
class CLIBanner:
    """Banner cyberpunk: logo ASCII gradient + 2 khung bảng neon."""

    LOGO_ART = [
        " ██   ██ ████████ ████████ ██████  ███    ███  █████  ███████ ",
        " ██   ██    ██       ██    ██   ██ ████  ████ ██   ██ ██      ",
        " ███████    ██       ██    ██████  ██ ████ ██ ███████ ███████ ",
        " ██   ██    ██       ██    ██      ██  ██  ██ ██   ██      ██ ",
        " ██   ██    ██       ██    ██      ██      ██ ██   ██ ███████ ",
        " ",
        "                                ©Copyright by Nguyễn Tấn Dũng",
    ]

    LOGO_LINE_GRADIENTS = [
        [(0, 255, 255), (0, 191, 255), (138, 43, 226)],
        [(0, 191, 255), (138, 43, 226), (255, 0, 255)],
        [(138, 43, 226), (255, 0, 255), (255, 20, 147)],
        [(255, 0, 255), (255, 20, 147), (255, 102, 0)],
        [(255, 20, 147), (255, 102, 0), (255, 255, 0)],
    ]

    BORDER_GRADIENT = [
        (0, 255, 255),
        (138, 43, 226),
        (255, 0, 255),
        (255, 20, 147),
    ]

    @classmethod
    def _render_logo(cls) -> str:
        """Kết xuất logo ASCII với gradient."""
        lines = []
        for i, line in enumerate(cls.LOGO_ART):
            grad = cls.LOGO_LINE_GRADIENTS[i % len(cls.LOGO_LINE_GRADIENTS)]
            lines.append(
                "  " + TerminalColor.gradient_text(line, grad)
            )
        return "\n".join(lines)

    @classmethod
    def _render_tagline_table(cls) -> str:
        """Khung bảng 5 tính năng cốt lõi."""
        tb = TableBuilder(
            border_color=TerminalColor.NEON_MAGENTA,
            gradient=cls.BORDER_GRADIENT,
        )

        taglines = [
            ("◆", "Network Engine", TerminalColor.NEON_CYAN),
            ("◆", "Socket-Based Client", TerminalColor.ELECTRIC_TEAL),
            ("◆", "TLS Secure Layer", TerminalColor.NEON_PINK),
            ("◆", "Async & Sync Unified", TerminalColor.ELECTRIC_PURPLE),
            ("◆", "Zero Dependencies", TerminalColor.NEON_YELLOW),
        ]

        rows = []
        for bullet, text, color in taglines:
            content = (
                f"{TerminalColor.NEON_ORANGE}{bullet}"
                f"{TerminalColor.RESET} "
                f"{TerminalColor.BOLD}{color}{text}"
                f"{TerminalColor.RESET}"
            )
            rows.append(tb.row(content))

        return tb.build(
            rows=rows,
            title="✦ HTTPMAS CORE FEATURES ✦",
            title_color=TerminalColor.NEON_MAGENTA,
        )

    @classmethod
    def _render_info_table(cls) -> str:
        """Khung bảng thông tin hệ thống."""
        tb = TableBuilder(
            border_color=TerminalColor.ELECTRIC_BLUE,
            gradient=cls.BORDER_GRADIENT,
        )

        rows = [
            tb.kv_row("Phiên bản", __version__,
                      value_color=TerminalColor.NEON_GREEN),
            tb.kv_row("Python", platform.python_version(),
                      value_color=TerminalColor.ELECTRIC_LIME),
            tb.kv_row("Hệ điều hành",
                      f"{platform.system()} {platform.release()}",
                      value_color=TerminalColor.ELECTRIC_TEAL),
            tb.kv_row("Kiến trúc", platform.machine(),
                      value_color=TerminalColor.NEON_YELLOW),
        ]

        return tb.build(
            rows=rows,
            title="SYSTEM INFO ",
            title_color=TerminalColor.ELECTRIC_BLUE,
        )

    @classmethod
    def render(cls, animate: bool = True) -> None:
        """Kết xuất banner đầy đủ."""
        blocks = [
            "",
            cls._render_logo(),
            "",
            cls._render_tagline_table(),
            "",
            cls._render_info_table(),
            "",
        ]

        if animate:
            cls._animate_output(blocks)
        else:
            print("\n".join(blocks))

    @classmethod
    def _animate_output(cls, blocks: list) -> None:
        """Hiển thị banner với hiệu ứng fade-in + spinner."""
        for i, block in enumerate(blocks):
            sys.stdout.write(block + "\n")
            sys.stdout.flush()
            time.sleep(0.15 if i == 0 else 0.03)

        frames = ["⣾", "", "⣻", "⢿", "⡿", "", "⣯", "⣷"]
        loading = (
            f"  {TerminalColor.ELECTRIC_PURPLE}"
            f"{TerminalColor.BOLD}Đang khởi tạo engine"
            f"{TerminalColor.RESET}"
        )
        for i in range(16):
            sys.stdout.write(
                f"\r{loading}{'.' * (i % 4)} "
                f"{frames[i % len(frames)]}  "
            )
            sys.stdout.flush()
            time.sleep(0.08)

        sys.stdout.write(
            f"\r  {TerminalColor.NEON_GREEN}"
            f"{TerminalColor.BOLD}✓ Sẵn sàng hoạt động!"
            f"{' ' * 30}{TerminalColor.RESET}\n\n"
        )
        sys.stdout.flush()


# ============================================================
# CLI APP
# ============================================================
class CLIApp:
    """Ứng dụng dòng lệnh chính của httpmas."""

    @staticmethod
    def clear_screen() -> None:
        """Xóa màn hình theo hệ điều hành."""
        if platform.system().lower() == "windows":
            os.system("cls")
        else:
            os.system("clear")

    @staticmethod
    def run_version() -> None:
        """Xử lý httpmas --version."""
        CLIApp.clear_screen()
        CLIBanner.render(animate=True)

    @staticmethod
    def run_help() -> None:
        """Trợ giúp trong 4 khung bảng neon đồng bộ màu."""

        # --- Khung 1: lệnh CLI (vàng → cam) ---
        tb1 = TableBuilder(
            border_color=TerminalColor.NEON_YELLOW,
            gradient=[(255, 255, 0), (255, 102, 0)],
        )
        rows1 = [
            tb1.kv_row("httpmas --version",
                       "Hiện phiên bản & banner",
                       label_color=TerminalColor.NEON_GREEN,
                       value_color=TerminalColor.NEUTRAL_WHITE),
            tb1.kv_row("httpmas --help",
                       "Hiện trợ giúp này",
                       label_color=TerminalColor.NEON_GREEN,
                       value_color=TerminalColor.NEUTRAL_WHITE),
            tb1.kv_row("httpmas --info",
                       "Thông tin hệ thống",
                       label_color=TerminalColor.NEON_GREEN,
                       value_color=TerminalColor.NEUTRAL_WHITE),
        ]
        table1 = tb1.build(
            rows=rows1, title="⌘ CLI COMMANDS",
            title_color=TerminalColor.NEON_YELLOW,
        )

        # --- Khung 2: sync (cyan → tím) ---
        tb2 = TableBuilder(
            border_color=TerminalColor.NEON_CYAN,
            gradient=[(0, 255, 255), (138, 43, 226)],
        )
        sync_lines = [
            (TerminalColor.ELECTRIC_PURPLE,
             "from httpmas import requests"),
            (TerminalColor.NEUTRAL_GRAY, ""),
            (TerminalColor.PASTEL_CYAN, "# GET request"),
            (TerminalColor.NEUTRAL_WHITE,
             "r = requests.get('https://example.com')"),
            (TerminalColor.NEUTRAL_WHITE,
             "print(r.status_code, r.text)"),
            (TerminalColor.NEUTRAL_GRAY, ""),
            (TerminalColor.PASTEL_CYAN, "# POST với JSON"),
            (TerminalColor.NEUTRAL_WHITE,
             "r = requests.post(url, json={'key': 'val'})"),
            
        ]
        rows2 = [
            tb2.row(f"{c}{t}{TerminalColor.RESET}")
            for c, t in sync_lines
        ]
        table2 = tb2.build(
            rows=rows2, title="SYNC USAGE",
            title_color=TerminalColor.NEON_CYAN,
        )

        # --- Khung 3: async (hồng → cam) ---
        tb3 = TableBuilder(
            border_color=TerminalColor.NEON_PINK,
            gradient=[(255, 20, 147), (255, 102, 0)],
        )
        async_lines = [
            (TerminalColor.ELECTRIC_PURPLE, "import asyncio"),
            (TerminalColor.NEUTRAL_GRAY, ""),
            (TerminalColor.ELECTRIC_PURPLE, "async def main():"),
            (TerminalColor.NEUTRAL_WHITE,
             "    r = await requests.async_get(url)"),
            (TerminalColor.NEUTRAL_WHITE,
             "    print(r.status_code)"),
            (TerminalColor.NEUTRAL_GRAY, ""),
            (TerminalColor.NEUTRAL_WHITE, "asyncio.run(main())"),
        ]
        rows3 = [
            tb3.row(f"{c}{t}{TerminalColor.RESET}")
            for c, t in async_lines
        ]
        table3 = tb3.build(
            rows=rows3, title="ASYNC USAGE",
            title_color=TerminalColor.NEON_PINK,
        )

        # --- Khung 4: lỗi (đỏ → cam) ---
        tb4 = TableBuilder(
            border_color=TerminalColor.NEON_RED,
            gradient=[(255, 0, 60), (255, 102, 0)],
        )
        err_lines = [
            (TerminalColor.ELECTRIC_PURPLE,
             "from httpmas import RequestsError"),
            (TerminalColor.NEUTRAL_GRAY, ""),
            (TerminalColor.ELECTRIC_PURPLE, "try:"),
            (TerminalColor.NEUTRAL_WHITE,
             "    r = requests.get(url)"),
            (TerminalColor.NEUTRAL_WHITE,
             "    r.raise_for_status()"),
            (TerminalColor.ELECTRIC_PURPLE,
             "except RequestsError as e:"),
            (TerminalColor.NEUTRAL_WHITE,
             "    print(e)  # Tự in màu RGB"),
        ]
        rows4 = [
            tb4.row(f"{c}{t}{TerminalColor.RESET}")
            for c, t in err_lines
        ]
        table4 = tb4.build(
            rows=rows4, title="ERROR HANDLING",
            title_color=TerminalColor.NEON_RED,
        )

        print()
        print(table1)
        print()
        print(table2)
        print()
        print(table3)
        print()
        print(table4)
        print()

    @staticmethod
    def run_info() -> None:
        """Thông tin hệ thống trong 4 khung bảng neon đồng bộ."""
        import ssl

        # --- Khung 1: thư viện (cyan → tím) ---
        tb1 = TableBuilder(
            border_color=TerminalColor.NEON_CYAN,
            gradient=[(0, 255, 255), (138, 43, 226)],
        )
        rows1 = [
            tb1.kv_row("Tên thư viện", "httpmas",
                       value_color=TerminalColor.NEON_YELLOW),
            tb1.kv_row("Phiên bản", __version__,
                       value_color=TerminalColor.NEON_GREEN),
            tb1.kv_row("Kiến trúc", "Socket-based, OOP",
                       value_color=TerminalColor.ELECTRIC_TEAL),
            tb1.kv_row("Dependencies", "Zero (stdlib only)",
                       value_color=TerminalColor.NEON_ORANGE),
            tb1.kv_row("License", "MIT",
                       value_color=TerminalColor.PASTEL_MINT),
        ]
        table1 = tb1.build(
            rows=rows1, title="LIBRARY",
            title_color=TerminalColor.NEON_CYAN,
        )

        # --- Khung 2: python (tím → magenta) ---
        tb2 = TableBuilder(
            border_color=TerminalColor.ELECTRIC_PURPLE,
            gradient=[(138, 43, 226), (255, 0, 255)],
        )
        rows2 = [
            tb2.kv_row("Phiên bản", platform.python_version(),
                       label_color=TerminalColor.PASTEL_LAVENDER,
                       value_color=TerminalColor.NEON_YELLOW),
            tb2.kv_row("Implementation",
                       platform.python_implementation(),
                       label_color=TerminalColor.PASTEL_LAVENDER,
                       value_color=TerminalColor.ELECTRIC_LIME),
            tb2.kv_row("Compiler",
                       platform.python_compiler().split()[0],
                       label_color=TerminalColor.PASTEL_LAVENDER,
                       value_color=TerminalColor.ELECTRIC_TEAL),
        ]
        table2 = tb2.build(
            rows=rows2, title="PYTHON RUNTIME",
            title_color=TerminalColor.ELECTRIC_PURPLE,
        )

        # --- Khung 3: hệ điều hành (hồng → cam) ---
        tb3 = TableBuilder(
            border_color=TerminalColor.NEON_PINK,
            gradient=[(255, 20, 147), (255, 102, 0)],
        )
        rows3 = [
            tb3.kv_row("Tên",
                       f"{platform.system()} {platform.release()}",
                       label_color=TerminalColor.PASTEL_PINK,
                       value_color=TerminalColor.ELECTRIC_LIME),
            tb3.kv_row("Kiến trúc", platform.machine(),
                       label_color=TerminalColor.PASTEL_PINK,
                       value_color=TerminalColor.NEON_YELLOW),
            tb3.kv_row("Processor",
                       platform.processor() or "N/A",
                       label_color=TerminalColor.PASTEL_PINK,
                       value_color=TerminalColor.ELECTRIC_TEAL),
        ]
        table3 = tb3.build(
            rows=rows3, title="OPERATING SYSTEM",
            title_color=TerminalColor.NEON_PINK,
        )

        # --- Khung 4: bảo mật (xanh lá → cyan) ---
        tb4 = TableBuilder(
            border_color=TerminalColor.NEON_GREEN,
            gradient=[(57, 255, 20), (0, 255, 255)],
        )
        try:
            max_tls = "TLS 1.3" if ssl.HAS_TLSv1_3 else "TLS 1.2"
        except AttributeError:
            max_tls = "TLS 1.2"
        rows4 = [
            tb4.kv_row("SSL Library", ssl.OPENSSL_VERSION[:38],
                       label_color=TerminalColor.PASTEL_MINT,
                       value_color=TerminalColor.ELECTRIC_LIME),
            tb4.kv_row("TLS tối đa", max_tls,
                       label_color=TerminalColor.PASTEL_MINT,
                       value_color=TerminalColor.NEON_YELLOW),
            tb4.kv_row("CA Verify", "CERT_REQUIRED (bắt buộc)",
                       label_color=TerminalColor.PASTEL_MINT,
                       value_color=TerminalColor.NEON_GREEN),
            tb4.kv_row("Hostname Check", "Enabled (SNI)",
                       label_color=TerminalColor.PASTEL_MINT,
                       value_color=TerminalColor.ELECTRIC_TEAL),
            tb4.kv_row("Cipher Suite", "ECDHE+AESGCM, CHACHA20",
                       label_color=TerminalColor.PASTEL_MINT,
                       value_color=TerminalColor.PASTEL_GOLD),
        ]
        table4 = tb4.build(
            rows=rows4, title="SECURITY",
            title_color=TerminalColor.NEON_GREEN,
        )

        print()
        print(table1)
        print()
        print(table2)
        print()
        print(table3)
        print()
        print(table4)
        print()

    @classmethod
    def main(cls) -> None:
        """Điểm vào chính của CLI."""
        args = sys.argv[1:]

        if not args or "--version" in args or "-v" in args:
            cls.run_version()
        elif "--help" in args or "-h" in args:
            cls.run_help()
        elif "--info" in args:
            cls.run_info()
        else:
            tb = TableBuilder(
                border_color=TerminalColor.NEON_RED,
                gradient=[(255, 0, 60), (255, 102, 0)],
            )
            rows = [
                tb.row(
                    f"{TerminalColor.NEON_RED}{TerminalColor.BOLD}"
                    f"✗ Lệnh không xác nhận: "
                    f"{TerminalColor.NEON_YELLOW}{args[0]}"
                    f"{TerminalColor.RESET}"
                ),
                tb.row(
                    f"{TerminalColor.NEUTRAL_WHITE}Dùng "
                    f"{TerminalColor.NEON_GREEN}httpmas --help"
                    f"{TerminalColor.NEUTRAL_WHITE} "
                    f"để xem trợ giúp.{TerminalColor.RESET}"
                ),
            ]
            print()
            print(tb.build(
                rows=rows, title="ERROR",
                title_color=TerminalColor.NEON_RED,
            ))
            print()


def cli_entry() -> None:
    """Điểm vào cho entry point trong setup.py."""
    CLIApp.main()