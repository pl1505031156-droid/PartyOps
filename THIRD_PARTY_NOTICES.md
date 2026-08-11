# 第三方开源软件声明

PartyOps 1.4.3 使用下列直接运行依赖；完整的直接与传递依赖可在
[Python CycloneDX SBOM](docs/sbom-python.cdx.json) 和
[前端 CycloneDX SBOM](docs/sbom-frontend.cdx.json) 中审计。版本由
`backend/requirements*.txt`、`backend/pyproject.toml` 与
`frontend/pnpm-lock.yaml` 锁定。文档字节不会因 AnyDoc 或 pdf-inspector
发送到 Firecrawl 或其他外部服务。

## Python 直接运行依赖

| 组件 | 固定版本 | 许可证 |
| --- | --- | --- |
| FastAPI / Starlette / Uvicorn | 0.140.7 / 1.3.1 / 0.35.0 | MIT / BSD-3-Clause / BSD-3-Clause |
| SQLAlchemy / Alembic | 2.0.43 / 1.16.5 | MIT |
| Pydantic Settings | 2.10.1 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| sse-starlette | 3.0.2 | BSD-3-Clause |
| python-docx / openpyxl | 1.2.0 / 3.1.5 | MIT |
| PyMuPDF | 1.26.0 | GNU AGPL-3.0 或 Artifex 商业许可；本项目采用前者 |
| Pillow | 12.3.0 | MIT-CMU |
| pytesseract | 0.3.13 | Apache-2.0 |
| cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause |
| defusedxml | 0.7.1 | PSF-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| NumPy / ONNX Runtime | 2.2.6 / 1.22.1 | BSD-3-Clause / MIT |
| tokenizers / hf-xet | 0.21.4 / 1.5.2 | Apache-2.0 |

PyMuPDF 以 AGPL-3.0 提供，PartyOps 自有代码仍按 GPL-3.0 授权；根据
GPLv3 第 13 节，GPLv3 与 AGPLv3 可以组合，但组合的软件需要遵守 AGPLv3
关于网络交互和对应源代码可得性的特别要求。因此应用“使用帮助”中提供了
当前对应源代码链接，发布二进制时必须同时保留本声明、源代码获取方式和
[GNU AGPLv3 正文](https://www.gnu.org/licenses/agpl-3.0.html)。如改用 Artifex
商业许可，应由发布方自行取得授权并更新本声明。本段是合规实施记录，不构成法律意见。

## 前端直接运行依赖

| 组件 | 固定版本 | 许可证 |
| --- | --- | --- |
| Vue / Vue Router / Pinia | 3.5.21 / 4.5.1 / 3.0.3 | MIT |
| Arco Design Vue | 2.57.0 | MIT |
| dayjs / lunar-typescript | 1.11.13 / 1.8.6 | MIT |
| markdown-it | 15.0.0 | MIT |
| `@firecrawl/anydoc-wasm` | 0.1.7 | MIT |
| `@firecrawl/pdf-inspector-wasm` | 0.1.3 | MIT；内含 Adobe CMaps（BSD-3-Clause） |

测试环境使用 `fake-indexeddb` 6.2.5（Apache-2.0）验证严格本机私有存储；该包是开发依赖，不进入生产前端运行包。

## 文件中心文档解析组件

| 组件 | 固定版本 | 用途 | 上游 | 许可证 |
| --- | --- | --- | --- | --- |
| `@firecrawl/anydoc-wasm` | 0.1.7 | Office、OpenDocument、RTF、EPUB、CSV 等文档转结构化 Markdown | <https://github.com/firecrawl/anydoc> | MIT |
| `@firecrawl/pdf-inspector-wasm` | 0.1.3 | PDF 类型、页面、布局、表格/分栏与结构化文字解析 | <https://github.com/firecrawl/pdf-inspector> | MIT；内含 Adobe CMaps，见下文 |
| `markdown-it` | 15.0.0 | 禁用 HTML 的安全 Markdown 阅读视图 | <https://github.com/markdown-it/markdown-it> | MIT |

## 重新生成依赖清单

在锁定依赖安装完成后运行 `scripts/generate-sbom.ps1`。发布门禁会同时执行
`pip-audit`、`pnpm audit --prod` 和许可证清单检查；SBOM 必须随每次依赖变更更新。

## AnyDoc

MIT License

Copyright (c) 2026 Sideguide Technologies Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## pdf-inspector

MIT License

Copyright (c) 2026 Firecrawl

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### Adobe CMaps

The WebAssembly binary embeds binary CMaps derived from Adobe CMap resources.

Copyright 1990-2009 Adobe Systems Incorporated. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

Redistributions of source code must retain the above copyright notice, this
list of conditions and the following disclaimer.

Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.

Neither the name of Adobe Systems Incorporated nor the names of its
contributors may be used to endorse or promote products derived from this
software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

## markdown-it

Copyright (c) 2014 Vitaly Puzrin, Alex Kocharin.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
