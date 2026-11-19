# 机器学习模块使用指南

## 📦 安装依赖

```bash
cd backend
pip install -r ml_requirements.txt
```

**必需依赖：**
- numpy
- pandas
- scikit-learn
- joblib

**可选依赖：**
- xgboost（用于 XGBoost 模型）

## 🚀 快速开始

### 1. 启动后端服务

```bash
cd backend
uvicorn main:app --port 8000 --reload
```

### 2. 测试机器学习模块

```bash
cd backend
python test_ml.py
```

这将运行完整的测试套件，包括：
- ✓ 模型训练（逻辑回归、随机森林）
- ✓ 单个和批量预测
- ✓ 模型保存和加载
- ✓ API 接口测试（需要后端服务运行）

## 📡 API 端点

### 训练模型

**POST** `/api/ml/train`

```json
{
  "flips": [
    {
      "bodyAtrRatio": 1.2,
      "flipDensity": 0.08,
      "wickRatio": 0.5,
      "trendAge": 35,
      "gapRatio": 0.2,
      "success": 1
    }
  ],
  "model_type": "random_forest",
  "test_size": 0.3,
  "model_params": {}
}
```

**响应：**
```json
{
  "success": true,
  "message": "模型训练成功！准确率: 72.00%",
  "evaluation": {
    "accuracy": 0.72,
    "precision": 0.68,
    "recall": 0.75,
    "f1Score": 0.71,
    "auc": 0.78,
    "confusionMatrix": {
      "truePositive": 45,
      "trueNegative": 32,
      "falsePositive": 12,
      "falseNegative": 11
    },
    "featureImportance": [
      {"feature": "Body/ATR", "importance": 0.28},
      {"feature": "Flip Density", "importance": 0.24}
    ]
  },
  "model_path": "./ml_models/flip_predictor_random_forest_20260919_123456.pkl"
}
```

### 预测单个 Flip

**POST** `/api/ml/predict`

```json
{
  "flip": {
    "bodyAtrRatio": 1.2,
    "flipDensity": 0.08,
    "wickRatio": 0.5,
    "trendAge": 35,
    "gapRatio": 0.2
  }
}
```

**响应：**
```json
{
  "label": 1,
  "probability": 0.78
}
```

### 批量预测

**POST** `/api/ml/predict/batch`

```json
{
  "flips": [
    {
      "bodyAtrRatio": 1.2,
      "flipDensity": 0.08,
      "wickRatio": 0.5,
      "trendAge": 35,
      "gapRatio": 0.2
    }
  ]
}
```

### 获取模型信息

**GET** `/api/ml/model/info`

**响应：**
```json
{
  "model_type": "random_forest",
  "trained_at": "2026-09-19T12:34:56",
  "train_size": 70,
  "test_size": 30,
  "evaluation": { ... }
}
```

### 加载已保存的模型

**POST** `/api/ml/model/load?model_path=./ml_models/xxx.pkl`

### 列出所有模型

**GET** `/api/ml/model/list`

## 🤖 支持的模型类型

| 模型 | model_type | 速度 | 准确率 | 可解释性 |
|------|-----------|------|--------|---------|
| 逻辑回归 | `logistic_regression` | ⚡⚡⚡ | ⭐⭐ | ⭐⭐⭐ |
| 随机森林 | `random_forest` | ⚡⚡ | ⭐⭐⭐ | ⭐⭐ |
| XGBoost | `xgboost` | ⚡ | ⭐⭐⭐⭐ | ⭐ |
| 神经网络 | `neural_network` | ⚡ | ⭐⭐⭐ | ⭐ |

**推荐：** `random_forest`（平衡性能与可解释性）

## 📊 特征说明

训练模型需要 5 个特征：

1. **bodyAtrRatio** - Body/ATR 比率
   - K 线实体大小相对于 ATR 的倍数
   - 范围：0.5 - 2.5
   - 过大：追高风险；过小：缺乏动能

2. **flipDensity** - Flip 密度
   - 近期 Flip 发生频率
   - 范围：0.05 - 0.3
   - 过高：震荡市场，假信号多

3. **wickRatio** - 影线比率
   - 影线长度 / Body 大小
   - 范围：0.3 - 1.5
   - 过大：犹豫不决，可能假突破

4. **trendAge** - 趋势年龄
   - 前序趋势持续的 K 线数量
   - 范围：10 - 100
   - 过大：趋势末期，易反转

5. **gapRatio** - Gap/ATR 比率
   - 跳空缺口相对于 ATR 的倍数
   - 范围：0 - 1.0
   - 过大：跳空易回补

## 💡 使用示例

### Python 脚本

```python
from ml_service import FlipPredictor

# 创建预测器
predictor = FlipPredictor()

# 准备训练数据
flips = [
    {
        'bodyAtrRatio': 1.2,
        'flipDensity': 0.08,
        'wickRatio': 0.5,
        'trendAge': 35,
        'gapRatio': 0.2,
        'success': 1,
    },
    # ... 更多数据
]

# 训练模型
evaluation = predictor.train(
    flips=flips,
    model_type='random_forest',
    test_size=0.3,
)

print(f"准确率: {evaluation['accuracy']:.2%}")

# 预测
test_flip = {
    'bodyAtrRatio': 1.1,
    'flipDensity': 0.09,
    'wickRatio': 0.6,
    'trendAge': 40,
    'gapRatio': 0.3,
}

label, prob = predictor.predict(test_flip)
print(f"预测: {'成功' if label == 1 else '失败'} (概率: {prob:.2%})")

# 保存模型
model_path = predictor.save('my_model')
print(f"模型已保存: {model_path}")
```

### JavaScript/前端集成

```javascript
// 训练模型
const trainResponse = await fetch('http://localhost:8000/api/ml/train', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    flips: flipsData,
    model_type: 'random_forest',
    test_size: 0.3,
  }),
});

const trainResult = await trainResponse.json();
console.log('准确率:', trainResult.evaluation.accuracy);

// 预测
const predictResponse = await fetch('http://localhost:8000/api/ml/predict', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    flip: {
      bodyAtrRatio: 1.2,
      flipDensity: 0.08,
      wickRatio: 0.5,
      trendAge: 35,
      gapRatio: 0.2,
    },
  }),
});

const predictResult = await predictResponse.json();
console.log('预测标签:', predictResult.label);
console.log('成功概率:', predictResult.probability);
```

## 📁 文件结构

```
backend/
├── ml_service.py          # 核心训练和预测逻辑
├── ml_api.py              # FastAPI 路由和接口
├── test_ml.py             # 测试脚本
├── ml_requirements.txt    # 依赖列表
├── ML_README.md           # 本文档
└── ml_models/             # 模型保存目录（自动创建）
    ├── flip_predictor_*.pkl
    └── flip_predictor_*_metadata.json
```

## 🔧 故障排查

### 问题 1: ImportError: No module named 'sklearn'

```bash
pip install scikit-learn
```

### 问题 2: XGBoost 不可用

如果不需要 XGBoost，可以只使用其他模型。要启用 XGBoost：

```bash
pip install xgboost
```

### 问题 3: 模型训练失败 "需要至少 10 条数据"

确保你的 Flip 数据集至少有 10 条记录。推荐 50+ 条以获得更好的性能。

### 问题 4: API 返回 404

检查：
1. 后端服务是否正在运行
2. 端口是否正确（默认 8000）
3. ml_api.py 是否正确导入到 main.py

## 📈 性能优化建议

1. **数据量**：至少 50 条训练数据，推荐 100+
2. **特征工程**：可以添加衍生特征（如 Body/Wick 比率）
3. **超参数调优**：使用 GridSearch 或 RandomSearch
4. **模型集成**：训练多个模型并投票决策
5. **定期重训练**：随着新数据积累，定期更新模型

## 🎯 下一步

1. 在前端采集真实 Flip 数据
2. 标注数据（成功/失败）
3. 训练模型并评估
4. 调整超参数优化性能
5. 部署到生产环境

---

**相关文档：**
- [前端机器学习模块](../frontend/src/pages/ResearchPage.jsx)
- [研究方向文档](../supertrend研究方向.md)
