import google.generativeai as genai
import pytest
import structlog

from flare_ai_social.ai import GeminiProvider
from flare_ai_social.prompts import (
    CHAIN_OF_THOUGHT_PROMPT,
    FEW_SHOT_PROMPT,
    ZERO_SHOT_PROMPT,
)
from flare_ai_social.settings import settings

# Test data
TEST_PROMPTS = [
    "Uhhh, sorry guys, did we forget we are building the tech for the future?",
    "Already have yield on my XRP.",
]


# Configure the API
genai.configure(api_key=settings.gemini_api_key)
logger = structlog.get_logger(__name__)


@pytest.fixture
def tuned_model() -> GeminiProvider:
    """Fixture to provide a configured tuned model"""
    model_name = f"tunedModels/{settings.tuned_model_name}"
    return GeminiProvider(
        settings.gemini_api_key,
        model_name=model_name,
    )


@pytest.fixture
def zero_shot_model() -> GeminiProvider:
    """Fixture to provide a zero-shot model"""
    return GeminiProvider(
        settings.gemini_api_key,
        model_name="gemini-1.5-flash",
        system_instruction=ZERO_SHOT_PROMPT,
    )


@pytest.fixture
def few_shot_model() -> GeminiProvider:
    """Fixture to provide a few-shot model"""
    return GeminiProvider(
        settings.gemini_api_key,
        model_name="gemini-1.5-flash",
        system_instruction=FEW_SHOT_PROMPT,
    )


@pytest.fixture
def chain_of_thought_model() -> GeminiProvider:
    """Fixture to provide a chain-of-thought model"""
    return GeminiProvider(
        settings.gemini_api_key,
        model_name="gemini-1.5-flash",
        system_instruction=CHAIN_OF_THOUGHT_PROMPT,
    )


def test_list_available_models() -> None:
    """Test listing available tuned models"""
    tuned_models = genai.list_tuned_models()
    model_names = [m.name for m in tuned_models]

    assert len(model_names) > 0
    assert f"tunedModels/{settings.tuned_model_name}" in model_names
    logger.info("available tuned models", tuned_models=model_names)


def test_get_model_info() -> None:
    """Test retrieving specific model information"""
    model_info = genai.get_tuned_model(name=f"tunedModels/{settings.tuned_model_name}")

    assert model_info is not None
    assert model_info.name == f"tunedModels/{settings.tuned_model_name}"
    logger.info("tuned model info", model_info=model_info)


@pytest.mark.parametrize("prompt", TEST_PROMPTS)
def test_tuned_model_generation(tuned_model: GeminiProvider, prompt: str) -> None:
    """Test generation with tuned model"""
    result = tuned_model.generate_content(prompt)

    assert result is not None
    assert result.text
    assert len(result.text) > 0
    logger.info("tuned_model", prompt=prompt, result=result.text)


def test_zero_shot_generation(zero_shot_model: GeminiProvider) -> None:
    """Test zero-shot prompting strategy"""
    for prompt in TEST_PROMPTS:
        result = zero_shot_model.generate_content(prompt)

        assert result is not None
        assert result.text
        assert len(result.text) > 0
        logger.info("zero-shot", prompt=prompt, result=result.text)


def test_few_shot_generation(few_shot_model: GeminiProvider) -> None:
    """Test few-shot prompting strategy"""
    for prompt in TEST_PROMPTS:
        result = few_shot_model.generate_content(prompt)

        assert result is not None
        assert result.text
        assert len(result.text) > 0
        logger.info("few-shot", prompt=prompt, result=result.text)


def test_chain_of_thought_generation(chain_of_thought_model: GeminiProvider) -> None:
    """Test chain-of-thought prompting strategy"""
    for prompt in TEST_PROMPTS:
        result = chain_of_thought_model.generate_content(prompt)

        assert result is not None
        assert result.text
        assert len(result.text) > 0
        logger.info("chain-of-thought", prompt=prompt, result=result.text)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
