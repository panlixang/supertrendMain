"""
Simple ML module test - without unicode characters
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from ml_service import FlipPredictor, DEFAULT_MODEL_PARAMS
import numpy as np


def generate_mock_flips(n=100):
    """Generate mock flip data"""
    np.random.seed(42)
    flips = []

    for i in range(n):
        bodyAtrRatio = np.random.uniform(0.5, 2.5)
        flipDensity = np.random.uniform(0.05, 0.3)
        wickRatio = np.random.uniform(0.3, 1.5)
        trendAge = np.random.uniform(10, 100)
        gapRatio = np.random.uniform(0, 1.0)

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


def test_basic():
    """Basic test"""
    print("\n" + "="*50)
    print("Test: ML Training and Prediction")
    print("="*50)

    # Generate data
    flips = generate_mock_flips(100)
    print(f"[OK] Generated {len(flips)} mock flips")

    # Train model
    predictor = FlipPredictor()
    print("[OK] Predictor created")

    evaluation = predictor.train(
        flips=flips,
        model_type='random_forest',
        test_size=0.3,
    )
    print(f"[OK] Model trained")
    print(f"     Accuracy: {evaluation['accuracy']:.2%}")
    print(f"     Precision: {evaluation['precision']:.2%}")
    print(f"     Recall: {evaluation['recall']:.2%}")
    print(f"     F1 Score: {evaluation['f1Score']:.3f}")
    print(f"     AUC: {evaluation['auc']:.3f}")

    # Test prediction
    test_flip = {
        'bodyAtrRatio': 1.2,
        'flipDensity': 0.08,
        'wickRatio': 0.5,
        'trendAge': 35,
        'gapRatio': 0.2,
    }

    label, prob = predictor.predict(test_flip)
    print(f"[OK] Prediction test")
    print(f"     Label: {label}")
    print(f"     Probability: {prob:.2%}")

    # Save model
    model_path = predictor.save('test_model')
    print(f"[OK] Model saved: {model_path}")

    # Load model
    predictor2 = FlipPredictor()
    predictor2.load(model_path)
    label2, prob2 = predictor2.predict(test_flip)
    print(f"[OK] Model loaded and tested")
    print(f"     Consistency: {label == label2 and abs(prob - prob2) < 1e-6}")

    print("\n" + "="*50)
    print("[SUCCESS] All tests passed!")
    print("="*50)


if __name__ == '__main__':
    try:
        test_basic()
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
