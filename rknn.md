## 模型导出
```bash
python export.py --weights="yolov7-tiny.pt" --simplify
cd rknn 
python export_rknn.py
```