import os
from dotenv import load_dotenv

load_dotenv()

NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
NEBIUS_BASE_URL = os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
# Model tiers, verified against GET {NEBIUS_BASE_URL}models on 24 Sep 2026. Token
# Factory retires serverless models without notice, so re-verify with
# `python scripts/list_models.py` before a demo.
NEBIUS_MODEL_FAST = os.getenv("NEBIUS_MODEL_FAST", "nvidia/Nemotron-3_5-Lightning")
NEBIUS_MODEL_REASONING = os.getenv("NEBIUS_MODEL_REASONING", "nvidia/nemotron-3-super-120b-a12b")
NEBIUS_MODELS = {"fast": NEBIUS_MODEL_FAST, "reasoning": NEBIUS_MODEL_REASONING}
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
OFFLINE = os.getenv("OFFLINE", "true").lower() == "true"
SEARCH_TIME_RANGE = os.getenv("SEARCH_TIME_RANGE", "week")
# Techo absoluto de creditos Tavily del mes. 0 lo desactiva, que es lo correcto en
# local. En un despliegue publico cualquiera puede pulsar "Actualizar en vivo", y una
# corrida cuesta 3 creditos: un techo de N permite (N - gasto actual) / 3 corridas.
TAVILY_USAGE_CEILING = int(os.getenv("TAVILY_USAGE_CEILING", "0") or 0)
