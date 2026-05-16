"""Simple billing stub using token accounting.

- Cost per token is set to $0.002 (example rate).
- Provides a function to calculate total cost from a TokenAccounting instance.
- `charge_user` is a placeholder that prints a message – replace with real Stripe logic later.
"""

from typing import Dict

TOKEN_COST = 0.002  # USD per token (example rate)


def calculate_cost(total_tokens: int) -> float:
    """Calculate the USD cost for the given number of tokens."""
    return round(total_tokens * TOKEN_COST, 4)


def charge_user(user_id: str, total_tokens: int) -> None:
    """Placeholder charge function.

    In a real implementation this would call the Stripe API (or another payment provider).
    For now it simply prints the amount that would be charged.
    """
    amount = calculate_cost(total_tokens)
    print(f"[Billing] Would charge user {user_id} ${amount:.4f} for {total_tokens} tokens.")


def report_and_charge(user_id: str, accounting_obj) -> Dict[str, float]:
    """Convenience wrapper: report total tokens, compute cost, and print placeholder charge.
    Returns a dict with ``total_tokens`` and ``cost``.
    """
    total = accounting_obj.total_tokens
    cost = calculate_cost(total)
    charge_user(user_id, total)
    return {"total_tokens": total, "cost_usd": cost}
