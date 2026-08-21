from google import genai
from google.genai import types


class GeminiProvider:
    """Thin wrapper around google-genai. Built as one implementation behind a
    common shape (generate(contents, system_instruction, tools)) so an
    OpenRouter provider can be added later without touching the graph."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate(
        self,
        contents: list[types.Content],
        system_instruction: str,
        tools: list[types.Tool],
    ) -> types.GenerateContentResponse:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
