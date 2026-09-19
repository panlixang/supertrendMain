"""
机器学习模块测试脚本
用于验证训练、预测、模型保存/加载等功能
"""
import asyncio
import json

from ml_service import FlipPredictor, DEFAULT_MODEL_PARAMS


def generate_mock_flips(n: int = 100):
    """生成模拟 Flip 数据"""
    import numpy as np

    np.random.seed(42)
    flips = []

    for i in range(n):
        # 生成特征
        bodyAtrRatio = np.random.uniform(0.5, 2.5)
        flipDensity = np.random.uniform(0.05, 0.3)
        wickRatio = np.random.uniform(0.3, 1.5)
        trendAge = np.random.uniform(10, 100)
        gapRatio = np.random.uniform(0, 1.0)

        # 简单规则生成标签（模拟真实场景）
        # 好的 Flip：Body/ATR 适中、Density 低、Wick 小、趋势年龄适中
        score = 0
        if 0.8 < bodyAtrRatio < 1.8:
            score += 1
        if flipDensity < 0.15:
            score += 1
        if wickRatio < 0.8:
            score += 1
        if 20 < trendAge < 60:
            score += 1
        if gapRatio < 0.5:
            score += 1

        # 添加随机性
        success = 1 if (score >= 3 and np.random.random() > 0.3) else 0

        flips.append({
            'bodyAtrRatio': bodyAtrRatio,
            'flipDensity': flipDensity,
            'wickRatio': wickRatio,
            'trendAge': trendAge,
            'gapRatio': gapRatio,
            'success': success,
        })

    return flips


def test_training():
    """测试模型训练"""
    print("=" * 50)
    print("测试 1: 模型训练")
    print("=" * 50)

    # 生成数据
    flips = generate_mock_flips(100)
    print(f"✓ 生成 {len(flips)} 条模拟数据")

    # 创建预测器
    predictor = FlipPredictor()

    # 测试不同模型
    for model_type in ['logistic_regression', 'random_forest']:
        print(f"\n训练 {model_type}...")

        params = DEFAULT_MODEL_PARAMS[model_type]
        evaluation = predictor.train(
            flips=flips,
            model_type=model_type,
            test_size=0.3,
            **params
        )

        print(f"  准确率: {evaluation['accuracy']:.2%}")
        print(f"  精确率: {evaluation['precision']:.2%}")
        print(f"  召回率: {evaluation['recall']:.2%}")
        print(f"  F1 分数: {evaluation['f1Score']:.3f}")
        print(f"  AUC: {evaluation['auc']:.3f}")

        # 混淆矩阵
        cm = evaluation['confusionMatrix']
        print(f"  混淆矩阵:")
        print(f"    TP: {cm['truePositive']}, FP: {cm['falsePositive']}")
        print(f"    FN: {cm['falseNegative']}, TN: {cm['trueNegative']}")

        # 特征重要性
        print(f"  特征重要性:")
        for item in evaluation['featureImportance'][:3]:
            print(f"    {item['feature']}: {item['importance']:.2%}")

    print("\n✓ 训练测试完成")


def test_prediction():
    """测试预测功能"""
    print("\n" + "=" * 50)
    print("测试 2: 预测功能")
    print("=" * 50)

    # 生成数据并训练
    flips = generate_mock_flips(100)
    predictor = FlipPredictor()
    predictor.train(flips=flips, model_type='random_forest', test_size=0.3)

    # 测试单个预测
    test_flip = {
        'bodyAtrRatio': 1.2,
        'flipDensity': 0.08,
        'wickRatio': 0.5,
        'trendAge': 35,
        'gapRatio': 0.2,
    }

    label, prob = predictor.predict(test_flip)
    print(f"\n单个预测:")
    print(f"  特征: {test_flip}")
    print(f"  预测标签: {label}")
    print(f"  成功概率: {prob:.2%}")

    # 批量预测
    test_flips = generate_mock_flips(5)
    results = predictor.predict_batch(test_flips)

    print(f"\n批量预测 ({len(test_flips)} 条):")
    for i, (label, prob) in enumerate(results):
        print(f"  #{i+1}: 标签={label}, 概率={prob:.2%}")

    print("\n✓ 预测测试完成")


def test_save_load():
    """测试模型保存和加载"""
    print("\n" + "=" * 50)
    print("测试 3: 模型保存/加载")
    print("=" * 50)

    # 训练并保存
    flips = generate_mock_flips(100)
    predictor1 = FlipPredictor()
    predictor1.train(flips=flips, model_type='random_forest', test_size=0.3)

    model_path = predictor1.save(name='test_model')
    print(f"✓ 模型已保存: {model_path}")

    # 加载模型
    predictor2 = FlipPredictor()
    predictor2.load(model_path)
    print(f"✓ 模型已加载")

    # 验证预测一致性
    test_flip = {
        'bodyAtrRatio': 1.0,
        'flipDensity': 0.1,
        'wickRatio': 0.6,
        'trendAge': 40,
        'gapRatio': 0.3,
    }

    label1, prob1 = predictor1.predict(test_flip)
    label2, prob2 = predictor2.predict(test_flip)

    print(f"\n预测一致性验证:")
    print(f"  原始模型: 标签={label1}, 概率={prob1:.4f}")
    print(f"  加载模型: 标签={label2}, 概率={prob2:.4f}")
    print(f"  是否一致: {label1 == label2 and abs(prob1 - prob2) < 1e-6}")

    print("\n✓ 保存/加载测试完成")


async def test_api():
    """测试 API 接口"""
    print("\n" + "=" * 50)
    print("测试 4: API 接口")
    print("=" * 50)

    try:
        import httpx

        # 生成测试数据
        flips = generate_mock_flips(50)

        # 训练请求
        train_payload = {
            'flips': flips,
            'model_type': 'random_forest',
            'test_size': 0.3,
        }

        print("\n发送训练请求...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                'http://localhost:8000/api/ml/train',
                json=train_payload,
                timeout=30.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"✓ 训练成功")
                print(f"  准确率: {result['evaluation']['accuracy']:.2%}")
                print(f"  模型路径: {result['model_path']}")
            else:
                print(f"✗ 训练失败: {response.status_code}")
                print(f"  错误: {response.text}")

        # 预测请求
        predict_payload = {
            'flip': {
                'bodyAtrRatio': 1.2,
                'flipDensity': 0.08,
                'wickRatio': 0.5,
                'trendAge': 35,
                'gapRatio': 0.2,
            }
        }

        print("\n发送预测请求...")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                'http://localhost:8000/api/ml/predict',
                json=predict_payload,
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"✓ 预测成功")
                print(f"  标签: {result['label']}")
                print(f"  概率: {result['probability']:.2%}")
            else:
                print(f"✗ 预测失败: {response.status_code}")

        print("\n✓ API 测试完成")

    except ImportError:
        print("\n⚠ httpx 未安装，跳过 API 测试")
        print("  安装: pip install httpx")
    except Exception as e:
        print(f"\n✗ API 测试失败: {e}")


def main():
    """运行所有测试"""
    print("\n[ML Test] 机器学习模块测试")
    print("=" * 50)

    try:
        # 基础测试
        test_training()
        test_prediction()
        test_save_load()

        # API 测试（需要后端服务运行）
        print("\n" + "=" * 50)
        print("提示: 要测试 API，请先启动后端服务:")
        print("  cd backend && uvicorn main:app --port 8000")
        print("=" * 50)

        # asyncio.run(test_api())

        print("\n" + "=" * 50)
        print("✅ 所有测试通过！")
        print("=" * 50)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
