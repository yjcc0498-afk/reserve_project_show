# Vision Study Kit

图形识别与几何拼图研究的公开脱敏版本。

## 内容

- `tasks/icon_task/`：48 类图标的标注、数据构建、CNN 训练、质检与检测训练代码。
- `models/release/icon_task/`：正式 CNN 权重及类别清单。检查点仅包含模型张量、48 类标签及 64px 输入尺寸；未携带训练路径、优化器或日志。
- `puzzle/`：掩模/形状缺口定位、背景归组还原、差分定位以及占位协议编排。
- `common/`：独立路径约定与字体加载。

## 使用

在本目录使用 Python 虚拟环境安装 `requirements.txt`。图标推理函数位于 `tasks/icon_task/cls_train.py`：`load_model_set()` 加载正式模型，`predict_probs_multi()` 返回分类概率。数据集不会公开；训练及标注需要自己的 `datasets/icon_task/`。

```powershell
python puzzle/puzzle_solver.py --help
python puzzle/puzzle_solver_v2.py --help
python tasks/icon_task/cls_train.py --help
```

拼图求解需要用户自行提供背景图、带透明通道的块图或参考库。坐标转换和服务端答案契约需要独立校准。

## 发布边界

站点名称、真实域名、接口名称与任务名已替换。公开版本不包含原始题图、标注数据、浏览器状态、账号、Cookie、真实 token、抓包与商业站点脚本。协议编排依赖的 `local_signature_provider` 和 `local_signature_oracle` 需要私有实现，未随本包发布，因此不宣称原站端到端可用。

48 类和输入尺寸由本次读取正式权重确认。旧文档的测试集指标没有作为本次实测结果引用。本次仅验证源码语法、模型加载与离线推理；公开内容仍可能被检索或识别。
