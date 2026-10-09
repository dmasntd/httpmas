"""
Build script cho httpmas C-extension.
Nếu build thất bại (thiếu compiler), httpmas vẫn hoạt động bằng Python fallback.
"""
import sys
from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext


class OptionalBuildExt(build_ext):
    """Cho phép build thành công ngay cả khi C extension compile thất bại."""

    def build_extension(self, ext, *a, **kw):
        try:
            super().build_extension(ext, *a, **kw)
        except Exception as e:
            print(
                f"\n{'='*60}\n"
                f"WARNING: C extension build failed: {e}\n"
                f"httpmas will use pure Python fallback (still works fine).\n"
                f"{'='*60}\n"
            )
            if ext in self.extensions:
                self.extensions.remove(ext)


extra_args = []
if sys.platform == "win32":
    extra_args = ["-O2"]
else:
    extra_args = ["-O2", "-Wall", "-Wno-unused-function"]


setup(
    ext_modules=[
        Extension(
            "httpmas._httpmas_fast",
            sources=["httpmas/_httpmas_fast.c"],
            extra_compile_args=extra_args,
        )
    ],
    cmdclass={"build_ext": OptionalBuildExt},
)