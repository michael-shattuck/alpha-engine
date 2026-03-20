import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "http://20.120.229.168:8899")
ETHEREUM_RPC_URL = os.getenv("ETHEREUM_RPC_URL", "http://13.91.71.124:8545")
ARBITRUM_RPC_URL = os.getenv("ARBITRUM_RPC_URL", "http://13.91.71.124:8547")
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "")

JUPITER_API = "https://lite-api.jup.ag/swap/v1"
JUPITER_PRICE_API = "https://api.jup.ag/price/v2"
DEFILLAMA_API = "https://yields.llama.fi"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JLP_POOL = "5BUwFW4nRbftYTDMbgxykoFWqWHPzahFSNAaaaJtVKsq"

ORCA_WHIRLPOOL_SOL_USDC = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
ORCA_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"

WEB_API_HOST = "0.0.0.0"
WEB_API_PORT = 8090

ORCHESTRATOR_INTERVAL = 30
PRICE_UPDATE_INTERVAL = 10
RISK_CHECK_INTERVAL = 15
FEE_COMPOUND_INTERVAL = 4 * 3600

DEFAULT_MODE = "paper"

DEFAULT_CAPITAL_ALLOCATION = {
    "leveraged_lp": 0.80,
    "volatile_pairs": 0.20,
}

RISK_LIMITS = {
    "max_drawdown_percent": 10.0,
    "max_single_strategy_allocation": 0.50,
    "min_single_strategy_allocation": 0.05,
    "circuit_breaker_loss_percent": 5.0,
    "circuit_breaker_window_hours": 1.0,
    "max_volatility_scale_back": 2.0,
    "sol_crash_threshold_percent": -15.0,
    "rebalance_drift_threshold": 0.10,
}
