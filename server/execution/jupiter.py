import base64
import logging

import httpx
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

from server.config import SOLANA_RPC_URL, JUPITER_API, SOL_MINT, USDC_MINT, JLP_POOL

log = logging.getLogger(__name__)

JLP_MINT = "27G8MtK7VtTcCHkpASjSDdkWWYfoqT6ggEuKidVJidD4"
JLP_POOL_ADDRESS = Pubkey.from_string(JLP_POOL)


class JupiterExecutor:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.rpc: AsyncClient | None = None
        self.http: httpx.AsyncClient | None = None

    async def start(self):
        self.rpc = AsyncClient(SOLANA_RPC_URL, commitment=Confirmed)
        self.http = httpx.AsyncClient(timeout=30.0)

    async def stop(self):
        if self.http:
            await self.http.aclose()
        if self.rpc:
            await self.rpc.close()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
    ) -> dict:
        resp = await self.http.get(
            f"{JUPITER_API}/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": slippage_bps,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def swap(
        self,
        wallet: Keypair,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
    ) -> dict:
        quote = await self.get_quote(input_mint, output_mint, amount, slippage_bps)

        if self.paper_mode:
            return {
                "status": "simulated",
                "input_mint": input_mint,
                "output_mint": output_mint,
                "in_amount": quote.get("inAmount"),
                "out_amount": quote.get("outAmount"),
                "price_impact": quote.get("priceImpactPct"),
                "signature": "simulated_signature",
            }

        swap_resp = await self.http.post(
            f"{JUPITER_API}/swap",
            json={
                "quoteResponse": quote,
                "userPublicKey": str(wallet.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            },
        )
        swap_resp.raise_for_status()

        tx_b64 = swap_resp.json()["swapTransaction"]
        tx_bytes = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("swap tx: %s", signature)

        return {
            "status": "submitted",
            "input_mint": input_mint,
            "output_mint": output_mint,
            "in_amount": quote.get("inAmount"),
            "out_amount": quote.get("outAmount"),
            "price_impact": quote.get("priceImpactPct"),
            "signature": signature,
        }

    async def get_jlp_price(self) -> float:
        quote = await self.get_quote(JLP_MINT, USDC_MINT, int(1e6), slippage_bps=100)
        return int(quote["outAmount"]) / 1e6

    async def deposit_jlp(self, wallet: Keypair, amount_sol: float) -> dict:
        lamports = int(amount_sol * 1e9)

        if self.paper_mode:
            quote = await self.get_quote(SOL_MINT, JLP_MINT, lamports)
            jlp_received = int(quote["outAmount"]) / 1e6
            return {
                "status": "simulated",
                "sol_deposited": amount_sol,
                "jlp_received": jlp_received,
                "signature": "simulated_signature",
            }

        return await self._swap_to_jlp(wallet, SOL_MINT, lamports)

    async def withdraw_jlp(self, wallet: Keypair, amount: float) -> dict:
        jlp_atoms = int(amount * 1e6)

        if self.paper_mode:
            quote = await self.get_quote(JLP_MINT, USDC_MINT, jlp_atoms)
            usdc_received = int(quote["outAmount"]) / 1e6
            return {
                "status": "simulated",
                "jlp_withdrawn": amount,
                "usdc_received": usdc_received,
                "signature": "simulated_signature",
            }

        return await self._swap_from_jlp(wallet, USDC_MINT, jlp_atoms)

    async def _swap_to_jlp(self, wallet: Keypair, input_mint: str, amount: int) -> dict:
        quote = await self.get_quote(input_mint, JLP_MINT, amount)

        swap_resp = await self.http.post(
            f"{JUPITER_API}/swap",
            json={
                "quoteResponse": quote,
                "userPublicKey": str(wallet.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            },
        )
        swap_resp.raise_for_status()

        tx_b64 = swap_resp.json()["swapTransaction"]
        tx_bytes = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("deposit_jlp tx: %s", signature)

        return {
            "status": "submitted",
            "sol_deposited": amount / 1e9,
            "jlp_received": int(quote["outAmount"]) / 1e6,
            "signature": signature,
        }

    async def _swap_from_jlp(self, wallet: Keypair, output_mint: str, jlp_atoms: int) -> dict:
        quote = await self.get_quote(JLP_MINT, output_mint, jlp_atoms)

        swap_resp = await self.http.post(
            f"{JUPITER_API}/swap",
            json={
                "quoteResponse": quote,
                "userPublicKey": str(wallet.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            },
        )
        swap_resp.raise_for_status()

        tx_b64 = swap_resp.json()["swapTransaction"]
        tx_bytes = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        result = await self.rpc.send_transaction(tx)
        signature = str(result.value)
        log.info("withdraw_jlp tx: %s", signature)

        return {
            "status": "submitted",
            "jlp_withdrawn": jlp_atoms / 1e6,
            "usdc_received": int(quote["outAmount"]) / 1e6,
            "signature": signature,
        }
