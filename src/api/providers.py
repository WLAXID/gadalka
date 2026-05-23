"""Метаданные провайдеров API для gadalka.

Каждый провайдер описан: что даёт, как использовать ключ,
официальный free tier лимит, релевантность для проекта.

Используется как «справочник» — никакой логики, только данные.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

KeyUsage = Literal[
    "query_param",      # ?apiKey=XXX
    "header_bearer",    # Authorization: Bearer XXX
    "header_x_api_key", # X-Api-Key: XXX
    "url",              # ключ уже полный URL (Chainstack, Alchemy)
    "basic_auth",       # HTTP Basic
]

Relevance = Literal[
    "core",      # обязательно используем в gadalka
    "useful",    # планируем использовать
    "backup",    # как fallback
    "ignore",    # есть в пуле, но нам не нужно
]


@dataclass(frozen=True)
class ProviderMeta:
    name: str
    relevance: Relevance
    usage: KeyUsage
    description: str
    base_url: str | None = None
    free_tier_note: str = ""


PROVIDERS: dict[str, ProviderMeta] = {
    # --- CORE (релевантные для gadalka) ---
    "alchemy": ProviderMeta(
        name="alchemy",
        relevance="core",
        usage="url",
        description="Polygon RPC + archive node. Для on-chain price-history через event logs CTF Exchange.",
        base_url=None,  # ключ — это полный URL
        free_tier_note="Free tier с лимитом compute units; ротация 115 ключей даёт значительный запас.",
    ),
    "chainstack": ProviderMeta(
        name="chainstack",
        relevance="core",
        usage="url",
        description="Альтернативный Polygon RPC. Backup к Alchemy.",
        free_tier_note="9 ключей в пуле — для baseline-нагрузок хватит.",
    ),
    "polygon": ProviderMeta(
        name="polygon",
        relevance="core",
        usage="query_param",
        description="Polygon.io — котировки BTC/ETH/stocks. Reference price feed для крипто-рынков Polymarket.",
        base_url="https://api.polygon.io",
        free_tier_note="5 req/min на free tier; ротация 40 ключей → ~200 req/min суммарно.",
    ),
    "newsapi": ProviderMeta(
        name="newsapi",
        relevance="core",
        usage="header_x_api_key",
        description="NewsAPI — заголовки и статьи. Сырьё для news-lag стратегии.",
        base_url="https://newsapi.org/v2",
        free_tier_note="Free tier 100 req/day; 49 ключей → ~4900 req/day.",
    ),
    "tavily": ProviderMeta(
        name="tavily",
        relevance="core",
        usage="header_bearer",
        description="Tavily — web search + extract. Универсальный research-источник.",
        base_url="https://api.tavily.com",
        free_tier_note="Free tier 1000 search/мес; 8 ключей.",
    ),
    "perplexity": ProviderMeta(
        name="perplexity",
        relevance="core",
        usage="header_bearer",
        description="Perplexity — LLM с web search. Для news → P(yes) features.",
        base_url="https://api.perplexity.ai",
        free_tier_note="1 ключ; экономим запросы.",
    ),
    "odds_api": ProviderMeta(
        name="odds_api",
        relevance="useful",
        usage="query_param",
        description="The Odds API — котировки спорт-букмекеров. Для опционального арб со sport-категорией.",
        base_url="https://api.the-odds-api.com",
        free_tier_note="Free tier 500 req/мес; 18 ключей.",
    ),
    # --- USEFUL (бонусные из пула, могут пригодиться) ---
    "coingecko": ProviderMeta(
        name="coingecko",
        relevance="backup",
        usage="header_x_api_key",
        description="Крипто прайсы. Backup к Polygon.io.",
        base_url="https://api.coingecko.com/api/v3",
        free_tier_note="Free tier 30 req/min; 67 ключей.",
    ),
    "fred": ProviderMeta(
        name="fred",
        relevance="useful",
        usage="query_param",
        description="FRED — экономические данные США (inflation, jobs, GDP). Для politics-рынков.",
        base_url="https://api.stlouisfed.org/fred",
        free_tier_note="Free, лимиты мягкие; 26 ключей.",
    ),
    "openai": ProviderMeta(
        name="openai",
        relevance="backup",
        usage="header_bearer",
        description="OpenAI LLM. Альтернатива Perplexity для генерации embeddings и features.",
        base_url="https://api.openai.com/v1",
        free_tier_note="12 ключей.",
    ),
    "deepseek": ProviderMeta(
        name="deepseek",
        relevance="backup",
        usage="header_bearer",
        description="DeepSeek — самый дешёвый LLM ($0.27/1M input). Для массовой обработки.",
        base_url="https://api.deepseek.com/v1",
        free_tier_note="4 ключа.",
    ),
    "bitquery": ProviderMeta(
        name="bitquery",
        relevance="backup",
        usage="header_x_api_key",
        description="Multichain GraphQL для on-chain данных. Альтернатива Dune.",
        base_url="https://graphql.bitquery.io",
        free_tier_note="14 ключей.",
    ),
    # --- Прочее в пуле — не релевантно для gadalka ---
    # helius, birdeye, shyft, triton  — Solana
    # etherscan, infura — Ethereum mainnet (Polymarket на Polygon)
    # moralis, quicknode — общие web3
    # finnhub, twelvedata, marketstack — фондовые данные
    # lunarcrush, santiment — крипто-социал
    # cohere — LLM
    # abuseipdb, stripe, postman — не наша задача
    # coinglass — деривативы
}


def relevance(provider: str) -> Relevance:
    """Релевантность провайдера для gadalka."""
    meta = PROVIDERS.get(provider)
    return meta.relevance if meta else "ignore"


def core_providers() -> list[str]:
    """Список core-провайдеров (обязательно подключаем)."""
    return [p.name for p in PROVIDERS.values() if p.relevance == "core"]
