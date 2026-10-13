# 素材来源与许可（assets/models）

仓库内 GLB 均为开源素材的派生加工品；原始下载件保留在 `_inbox/`（已 gitignore）。
加工脚本在 `outputs/`（bake_skin.py / build_infantry.py / build_tank.py / build_humvee.py）。

| 文件 | 来源 | 许可 | 加工 |
|---|---|---|---|
| tank.glb | Quaternius「Animated Tanks」Tank2（经 trebeljahr/quaternius-showcase 单文件镜像，jsDelivr 直链） | CC0 1.0（无需署名） | 缩放定长 9.8m、Y-up→Z-up、接地、五档军绿顶点色+顶光明暗烘焙 |
| ifv.glb | CesiumJS 示例资产 GroundVehicle（6×6 悍马；mrdoob 无关，CesiumGS/cesium 仓库 Apps/SampleData） | Apache-2.0（© Analytical Graphics, Inc. / Cesium 贡献者） | 删低 LOD、根节点矩阵转 Z-up/接地、metallic=0/roughness=0.9 |
| atgm_team.glb | three.js 示例模型 Soldier.glb（mrdoob/three.js examples/models/gltf） | MIT（© 2010-2024 three.js 作者，许可全文附下） | Idle 动画 t=1s 蒙皮烘焙为静态持枪步兵、1.8m 定高、Y-up→Z-up、接地 |

## three.js MIT License

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

## Apache-2.0

ifv.glb 的来源遵循 Apache License 2.0（http://www.apache.org/licenses/LICENSE-2.0）。
