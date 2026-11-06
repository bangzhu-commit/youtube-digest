# Third-party notices

## Defuddle

Native YouTube player-client, caption-format and transcript DOM fallback ideas adapted from Defuddle 0.19.4 by Steph Ango. This is a small browser-specific implementation; the extension does not execute the Defuddle CLI, bundle the entire library, or call its hosted service.

Source: https://github.com/kepano/defuddle/blob/0.19.4/src/extractors/youtube.ts

Related Skill: https://github.com/kepano/obsidian-skills/blob/main/skills/defuddle/SKILL.md

MIT License

Copyright (c) 2025 Steph Ango (@kepano)

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

## baoyu-translate

Translation principles, terminology and review workflow adapted from baoyu-translate by Jim Liu, version 1.117.3, pinned commit 441ca307a60c594e8eda0ac156609503687544c0.

Source: https://github.com/JimLiu/baoyu-skills/tree/main/skills/baoyu-translate

The extension implements a compact, two-call subtitle workflow and adapts its filler-removal and source-review principles for Chinese podcast reading. It does not execute the local Skill or copy personal configuration files into the package.

MIT License

Copyright (c) 2026 Jim Liu

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


## Optional local speaker diarization

The explicit local setup installs sherpa-onnx and sherpa-onnx-core 2.0.1 from PyPI into a separate environment. The extension ZIP does not bundle the runtime or model weights. These packages are distributed under Apache-2.0; see https://github.com/k2-fsa/sherpa-onnx and its LICENSE.

The setup pins and verifies an ONNX conversion of pyannote segmentation 3.0 (MIT, Copyright 2022 CNRS) at https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/tree/9403a6902bb58e3d5ae8c7e77c3422de279db2e0, and the 3D-Speaker Chinese ERes2Net speaker-embedding model (Apache-2.0) at https://huggingface.co/csukuangfj/speaker-embedding-models/tree/0743f301363dec56491a490f6d6cbc9d67f9a3bf. The upstream 3D-Speaker project is https://github.com/modelscope/3D-Speaker. Model license files are saved alongside the local installation.

Only these acoustic models analyze the saved episode audio for anonymous speaker grouping. No speaker-recognition code, model weights, or credentials from an unrelated local application are copied into the release.
