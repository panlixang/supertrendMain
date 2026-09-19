"""
FastAPI 服务 - 机器学习 API 接口
提供训练、预测、模型管理等功能
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .ml_service import DEFAULT_MODEL_PARAMS, FlipPredictor

# 创建路由
ml_router = APIRouter(prefix="/api/ml", tags=["machine-learning"])

# 全局模型实例
predictor = FlipPredictor()


# ===== 请求/响应模型 =====

class FlipData(BaseModel):
    """Flip 数据模型"""
    bodyAtrRatio: float = Field(..., description="Body/ATR 比率")
    flipDensity: float = Field(..., description="Flip 密度")
    wickRatio: float = Field(..., description="影线比率")
    trendAge: float = Field(..., description="趋势年龄")
    gapRatio: float = Field(..., description="Gap/ATR 比率")
    success: Optional[int] = Field(None, description="标签：1=成功，0=失败")
    confirmed: Optional[int] = Field(None, description="备用标签")


class TrainRequest(BaseModel):
    """训练请求"""
    flips: List[FlipData] = Field(..., description="Flip 数据列表")
    model_type: str = Field('random_forest', description="模型类型")
    test_size: float = Field(0.3, ge=0.1, le=0.5, description="测试集比例")
    model_params: Optional[Dict] = Field(None, description="模型超参数")


class TrainResponse(BaseModel):
    """训练响应"""
    success: bool
    message: str
    evaluation: Optional[Dict] = None
    model_path: Optional[str] = None


class PredictRequest(BaseModel):
    """预测请求"""
    flip: FlipData = Field(..., description="单个 Flip 数据")


class PredictResponse(BaseModel):
    """预测响应"""
    label: int = Field(..., description="预测标签：1=成功，0=失败")
    probability: float = Field(..., description="成功概率 [0, 1]")


class BatchPredictRequest(BaseModel):
    """批量预测请求"""
    flips: List[FlipData] = Field(..., description="Flip 数据列表")


class BatchPredictResponse(BaseModel):
    """批量预测响应"""
    predictions: List[Dict] = Field(..., description="预测结果列表")


class ModelInfo(BaseModel):
    """模型信息"""
    model_type: Optional[str] = None
    trained_at: Optional[str] = None
    train_size: Optional[int] = None
    test_size: Optional[int] = None
    evaluation: Optional[Dict] = None


# ===== API 端点 =====

@ml_router.post("/train", response_model=TrainResponse)
async def train_model(request: TrainRequest):
    """
    训练模型

    Args:
        request: 训练请求

    Returns:
        训练结果和评估指标
    """
    try:
        # 转换为字典列表
        flips_data = [flip.dict() for flip in request.flips]

        # 获取模型参数
        model_params = request.model_params or DEFAULT_MODEL_PARAMS.get(
            request.model_type, {}
        )

        # 训练模型
        evaluation = predictor.train(
            flips=flips_data,
            model_type=request.model_type,
            test_size=request.test_size,
            **model_params
        )

        # 保存模型
        model_path = predictor.save(name=f"flip_predictor_{request.model_type}")

        return TrainResponse(
            success=True,
            message=f"模型训练成功！准确率: {evaluation['accuracy']:.2%}",
            evaluation=evaluation,
            model_path=model_path,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"训练失败: {str(e)}")


@ml_router.post("/predict", response_model=PredictResponse)
async def predict_flip(request: PredictRequest):
    """
    预测单个 Flip

    Args:
        request: 预测请求

    Returns:
        预测标签和概率
    """
    try:
        if predictor.model is None:
            raise HTTPException(status_code=400, detail="模型未训练，请先训练模型")

        flip_data = request.flip.dict()
        label, probability = predictor.predict(flip_data)

        return PredictResponse(label=label, probability=probability)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预测失败: {str(e)}")


@ml_router.post("/predict/batch", response_model=BatchPredictResponse)
async def predict_batch(request: BatchPredictRequest):
    """
    批量预测

    Args:
        request: 批量预测请求

    Returns:
        预测结果列表
    """
    try:
        if predictor.model is None:
            raise HTTPException(status_code=400, detail="模型未训练，请先训练模型")

        flips_data = [flip.dict() for flip in request.flips]
        results = predictor.predict_batch(flips_data)

        predictions = [
            {'label': label, 'probability': prob}
            for label, prob in results
        ]

        return BatchPredictResponse(predictions=predictions)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量预测失败: {str(e)}")


@ml_router.get("/model/info", response_model=ModelInfo)
async def get_model_info():
    """
    获取当前模型信息

    Returns:
        模型元数据
    """
    if predictor.model is None:
        return ModelInfo()

    metadata = predictor.metadata
    return ModelInfo(
        model_type=metadata.get('model_type'),
        trained_at=metadata.get('trained_at'),
        train_size=metadata.get('train_size'),
        test_size=metadata.get('test_size'),
        evaluation=metadata.get('evaluation'),
    )


@ml_router.post("/model/load")
async def load_model(model_path: str):
    """
    加载已保存的模型

    Args:
        model_path: 模型文件路径

    Returns:
        加载结果
    """
    try:
        predictor.load(model_path)
        return {
            'success': True,
            'message': f"模型加载成功: {model_path}",
            'metadata': predictor.metadata,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"模型文件不存在: {model_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载失败: {str(e)}")


@ml_router.get("/model/list")
async def list_models():
    """
    列出所有已保存的模型

    Returns:
        模型列表
    """
    try:
        model_files = list(predictor.model_dir.glob("*.pkl"))
        models = []

        for model_file in sorted(model_files, reverse=True):
            # 读取元数据
            metadata_file = model_file.with_suffix('.json').parent / (
                model_file.stem + '_metadata.json'
            )

            metadata = {}
            if metadata_file.exists():
                import json
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)

            models.append({
                'path': str(model_file),
                'name': model_file.stem,
                'size': model_file.stat().st_size,
                'created_at': metadata.get('trained_at', 'Unknown'),
                'model_type': metadata.get('model_type', 'Unknown'),
                'accuracy': metadata.get('evaluation', {}).get('accuracy'),
            })

        return {'models': models}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")
