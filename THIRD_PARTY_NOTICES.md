# Third-party components

The project's MIT license does not replace licenses for third-party components.

## Bundled browser assets

These assets are included in the source tree and run locally without a CDN:

| Component | Bundled version | Included license information |
| --- | --- | --- |
| Marked | 15.0.12 | [LICENSE-marked.txt](web/vendor/LICENSE-marked.txt), including the Marked MIT notice and upstream notices |
| DOMPurify | 3.4.5 | [LICENSE-dompurify.txt](web/vendor/LICENSE-dompurify.txt); the JavaScript header identifies Apache 2.0 and MPL 2.0 |

Upstream distribution URLs and integrity values are recorded in [web/vendor/README.md](web/vendor/README.md). Preserve the upstream license files and JavaScript notices when redistributing these assets.

## Supplemental runtime notices

The following exact upstream texts supplement the installed wheels' license files:

- [proxy_tools upstream notice](web/vendor/LICENSE-proxy_tools.txt). Its 0.1.0 wheel metadata says MIT, but the upstream file contains a BSD redistribution notice. The upstream text is preserved rather than replacing it with an invented MIT notice.
- [CPython 3.12.14 incorporated-software acknowledgements](web/vendor/LICENSE-CPython-incorporated.txt), including libffi and libmpdec.
- [OpenSSL 3.5.8](web/vendor/LICENSE-OpenSSL.txt).
- [zlib 1.3.2](web/vendor/LICENSE-zlib.txt).
- [Expat 2.8.3](web/vendor/LICENSE-expat.txt).

Official source links, upstream Git blob identifiers, and file SHA-256 values are recorded in [web/vendor/LICENSE-SOURCES.md](web/vendor/LICENSE-SOURCES.md). The bundled Python runtime's own license is also included in the portable release's `licenses` directory.

## Python and desktop dependencies

The source uses packages declared in `requirements.txt`, `requirements-desktop.txt`, and `requirements-build.txt`. They are installed separately and retain their own licenses. Windows binaries additionally include the Python runtime and the components selected by PyInstaller.

The portable release includes a `licenses` directory containing the runtime and installed dependency license information gathered by the packaging script. Review that directory when creating a release: an installed package's metadata can identify its license even when its wheel does not include the complete license text. Supplement missing notices from the corresponding upstream distribution before distributing that dependency.

Microsoft Edge WebView2 is a separately installed runtime; it is not part of the project's MIT-licensed code.

The first-run installer progress window in 0.1.5 also bundles Tcl/Tk 8.6.12. Its upstream notices are included as [Tcl](web/vendor/LICENSE-tcl.txt) and [Tk](web/vendor/LICENSE-tk.txt).
