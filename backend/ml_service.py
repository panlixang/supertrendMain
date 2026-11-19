"""
机器学习服务 - Flip 质量预测模型
支持多种算法：逻辑回归、随机森林、XGBoost、神经网络
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

# 可选：XGBoost（需要单独安装）
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False


class FlipPredictor:
    """Flip 质量预测器"""

    FEATURE_NAMES = [
        'bodyAtrRatio',
        'flipDensity',
        'wickRatio',
        'trendAge',
        'gapRatio',
    ]

    MODEL_TYPES = {
        'logistic_regression': LogisticRegression,
        'random_forest': RandomForestClassifier,
        'neural_network': MLPClassifier,
    }

    def __init__(self, model_dir: str = './ml_models'):
        """
        初始化 Flip 预测器

        Args:
            model_dir: 模型保存目录
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(exist_ok=True)

        self.model = None
        self.scaler = None
        self.model_type = None
        self.metadata = {}

    def prepare_data(
        self,
        flips: List[Dict],
        test_size: float = 0.3,
        random_state: int = 42
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        准备训练数据

        Args:
            flips: Flip 数据列表，每个包含特征和标签
            test_size: 测试集比例
            random_state: 随机种子

        Returns:
            X_train, X_test, y_train, y_test
        """
        # 提取特征
        X = []
        y = []

        for flip in flips:
            features = [flip.get(feat, 0) for feat in self.FEATURE_NAMES]
            X.append(features)

            # 标签：1=成功，0=失败
            # 假设 flip 中有 'success' 字段，或者根据其他字段推断
            label = flip.get('success', flip.get('confirmed', 0))
            y.append(1 if label else 0)

        X = np.array(X)
        y = np.array(y)

        # 划分训练集和测试集
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=random_state, stratify=y
        )

        # 标准化
        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        X_test = self.scaler.transform(X_test)

        return X_train, X_test, y_train, y_test

    def train(
        self,
        flips: List[Dict],
        model_type: str = 'random_forest',
        test_size: float = 0.3,
        **model_params
    ) -> Dict:
        """
        训练模型

        Args:
            flips: Flip 数据列表
            model_type: 模型类型
            test_size: 测试集比例
            **model_params: 模型超参数

        Returns:
            评估结果字典
        """
        if len(flips) < 10:
            raise ValueError("需要至少 10 条数据进行训练")

        # 准备数据
        X_train, X_test, y_train, y_test = self.prepare_data(flips, test_size)

        # 创建模型
        if model_type == 'xgboost':
            if not XGBOOST_AVAILABLE:
                raise ValueError("XGBoost 未安装，请运行: pip install xgboost")
            self.model = xgb.XGBClassifier(**model_params)
        elif model_type in self.MODEL_TYPES:
            ModelClass = self.MODEL_TYPES[model_type]
            self.model = ModelClass(**model_params)
        else:
            raise ValueError(f"不支持的模型类型: {model_type}")

        self.model_type = model_type

        # 训练
        self.model.fit(X_train, y_train)

        # 评估
        y_pred = self.model.predict(X_test)
        y_pred_proba = self.model.predict_proba(X_test)[:, 1]

        # 计算指标
        evaluation = {
            'accuracy': float(accuracy_score(y_test, y_pred)),
            'precision': float(precision_score(y_test, y_pred, zero_division=0)),
            'recall': float(recall_score(y_test, y_pred, zero_division=0)),
            'f1Score': float(f1_score(y_test, y_pred, zero_division=0)),
            'auc': float(roc_auc_score(y_test, y_pred_proba)),
            'confusionMatrix': self._format_confusion_matrix(
                confusion_matrix(y_test, y_pred)
            ),
            'featureImportance': self._get_feature_importance(),
        }

        # 保存元数据
        self.metadata = {
            'model_type': model_type,
            'trained_at': datetime.now().isoformat(),
            'train_size': len(X_train),
            'test_size': len(X_test),
            'evaluation': evaluation,
            'model_params': model_params,
        }

        return evaluation

    def predict(self, flip: Dict) -> Tuple[int, float]:
        """
        预测单个 Flip

        Args:
            flip: Flip 特征字典

        Returns:
            (预测标签, 成功概率)
        """
        if self.model is None:
            raise ValueError("模型未训练，请先调用 train()")

        # 提取特征
        features = np.array([[flip.get(feat, 0) for feat in self.FEATURE_NAMES]])

        # 标准化
        features = self.scaler.transform(features)

        # 预测
        label = int(self.model.predict(features)[0])
        proba = float(self.model.predict_proba(features)[0, 1])

        return label, proba

    def predict_batch(self, flips: List[Dict]) -> List[Tuple[int, float]]:
        """批量预测"""
        return [self.predict(flip) for flip in flips]

    def save(self, name: str = 'flip_predictor'):
        """
        保存模型

        Args:
            name: 模型名称
        """
        if self.model is None:
            raise ValueError("模型未训练，无法保存")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        model_path = self.model_dir / f"{name}_{timestamp}.pkl"

        # 保存模型和 scaler
        joblib.dump({
            'model': self.model,
            'scaler': self.scaler,
            'model_type': self.model_type,
            'metadata': self.metadata,
        }, model_path)

        # 保存元数据到 JSON
        metadata_path = self.model_dir / f"{name}_{timestamp}_metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)

        return str(model_path)

    def load(self, model_path: str):
        """
        加载模型

        Args:
            model_path: 模型文件路径
        """
        data = joblib.load(model_path)

        self.model = data['model']
        self.scaler = data['scaler']
        self.model_type = data['model_type']
        self.metadata = data.get('metadata', {})

    def _format_confusion_matrix(self, cm: np.ndarray) -> Dict:
        """格式化混淆矩阵"""
        return {
            'truePositive': int(cm[1, 1]),
            'trueNegative': int(cm[0, 0]),
            'falsePositive': int(cm[0, 1]),
            'falseNegative': int(cm[1, 0]),
        }

    def _get_feature_importance(self) -> List[Dict]:
        """获取特征重要性"""
        if hasattr(self.model, 'feature_importances_'):
            importances = self.model.feature_importances_
        elif hasattr(self.model, 'coef_'):
            importances = np.abs(self.model.coef_[0])
        else:
            # 对于神经网络等没有内置特征重要性的模型，返回均匀分布
            importances = np.ones(len(self.FEATURE_NAMES)) / len(self.FEATURE_NAMES)

        # 归一化到 [0, 1]
        importances = importances / importances.sum()

        feature_names_display = [
            'Body/ATR',
            'Flip Density',
            'Wick Ratio',
            '趋势年龄',
            'Gap/ATR',
        ]

        return [
            {'feature': name, 'importance': float(imp)}
            for name, imp in sorted(
                zip(feature_names_display, importances),
                key=lambda x: x[1],
                reverse=True
            )
        ]


# 默认模型参数
DEFAULT_MODEL_PARAMS = {
    'logistic_regression': {
        'max_iter': 1000,
        'random_state': 42,
    },
    'random_forest': {
        'n_estimators': 100,
        'max_depth': 10,
        'min_samples_split': 5,
        'min_samples_leaf': 2,
        'random_state': 42,
    },
    'xgboost': {
        'n_estimators': 100,
        'max_depth': 6,
        'learning_rate': 0.1,
        'random_state': 42,
    },
    'neural_network': {
        'hidden_layer_sizes': (64, 32),
        'max_iter': 500,
        'random_state': 42,
    },
}
