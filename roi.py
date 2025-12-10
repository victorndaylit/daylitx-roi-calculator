from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# -----------------------------
# Tiering and pricing (ARR-based)
# -----------------------------
# Thresholds and annual pricing confirmed:
# - Small: < $25,000,000 ARR  -> $12,000 / year
# - Middle market: $25–50M ARR -> $60,000 / year
# - Enterprise: > $50,000,000  -> $100,000 / year
TIERS = [
    ("Small", 0.0, 25_000_000.0, 12_000.0),
    ("Middle market", 25_000_000.0, 50_000_000.0, 60_000.0),
    ("Enterprise", 50_000_000.0, float("inf"), 100_000.0),
]


# -----------------------------
# Industry benchmarks (source: Damodaran, NYU Stern, Jan 2025)
# https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/wcdata.html
# DSO = Acc Rec/Sales × 365
# -----------------------------
INDUSTRY_DATA = {
    "Retail Distributors": {
        "acc_rec_to_sales_pct": 0.1216,
        "benchmark_dso_days": round(0.1216 * 365),  # 44 days
    },
    "Chemical (Specialty)": {
        "acc_rec_to_sales_pct": 0.1764,
        "benchmark_dso_days": round(0.1764 * 365),  # 64 days
    },
    "Hospitals/Healthcare Facilities": {
        "acc_rec_to_sales_pct": 0.1447,
        "benchmark_dso_days": round(0.1447 * 365),  # 53 days
    },
    "Business & Consumer Services": {
        "acc_rec_to_sales_pct": 0.1829,
        "benchmark_dso_days": round(0.1829 * 365),  # 67 days
    },
}


def get_industry_benchmark_dso(industry: str) -> float | None:
    """
    Returns the benchmark DSO for a given industry, or None if not found.
    """
    if industry in INDUSTRY_DATA:
        return INDUSTRY_DATA[industry]["benchmark_dso_days"]
    return None


def get_available_industries() -> list[str]:
    """Returns list of industries with benchmark data."""
    return list(INDUSTRY_DATA.keys())


# -----------------------------
# Input and configuration models
# -----------------------------
@dataclass
class Inputs:
    """User-provided business inputs (units noted in comments)."""

    industry: str                     # Industry label, informational for now
    annual_revenue: float             # USD/year (ARR)
    ar_headcount: int                 # Number of FTEs managing A/R
    current_dso_days: float           # Days Sales Outstanding (days)
    monthly_invoices: int             # Count per month
    fte_salary_base: float            # USD/year per FTE (base salary)
    bad_debt_pct: float               # e.g., 0.01 for 1% of A/R balance annually


@dataclass
class Assumptions:
    """Model assumptions. Tweak these to reflect expected Daylit X impact."""

    cost_of_capital_annual_pct: float = 0.045        # Used to value freed cash annually
    dso_reduction_relative_pct: float = 0.40        # % reduction applied to current DSO
    bad_debt_reduction_relative_pct: float = 0.40   # Relative reduction vs baseline bad debt
    productivity_time_saved_pct: float = 0.50       # % of A/R time saved
    # Invoice/time-based assumptions
    hours_per_fte_per_year: int = 2000
    working_days_per_year: int = 365
    percentage_of_time_on_invoices: float = 0.80       # % of an FTE spends on invoices


@dataclass
class Results:
    """Computation outputs."""

    roi_pct: float                               # Percentage; e.g., 150.0 means 150%
    cash_flow_improvement_usd: float
    annualized_employee_savings_usd: float
    productivity_hours_saved: float
    bad_debt_savings_usd: float
    opportunity_cost_usd: float                  # What client loses by not capturing these savings
    tier: str
    annual_price_usd: float


# -----------------------------
# Tiering logic
# -----------------------------
def determine_tier_and_price(annual_revenue_arr: float) -> Tuple[str, float]:
    """
    Determine the pricing tier and annual price from ARR.
    - Small: revenue < 25,000,000
    - Middle market: 25,000,000 <= revenue < 50,000,000
    - Enterprise: revenue >= 50,000,000
    """
    for name, lower, upper, price in TIERS:
        if lower <= annual_revenue_arr < upper:
            return name, price
    # Fallback shouldn't be hit due to 'inf' upper bound
    return "Enterprise", TIERS[-1][3]


# -----------------------------
# Core calculations
# -----------------------------
def compute_cash_flow_improvement(inputs: Inputs, assumptions: Assumptions) -> float:
    """
    Freed working capital from DSO reduction (no cost-of-capital multiplier).
    """
    # Convert percentage reduction into days reduced using current DSO
    days_reduced = inputs.current_dso_days * assumptions.dso_reduction_relative_pct
    average_daily_revenue = inputs.annual_revenue / assumptions.working_days_per_year
    freed_cash_balance = average_daily_revenue * days_reduced
    return max(0.0, freed_cash_balance)


def compute_annualized_employee_savings(inputs: Inputs, assumptions: Assumptions) -> float:
    """
    Savings from reduced effort by the A/R team.
    """
    hourly_wage = inputs.fte_salary_base / assumptions.hours_per_fte_per_year
    time_spent_on_invoices = assumptions.hours_per_fte_per_year * assumptions.percentage_of_time_on_invoices
    savings = inputs.ar_headcount * time_spent_on_invoices * assumptions.productivity_time_saved_pct * hourly_wage
    return max(0.0, savings)


def compute_productivity_hours_saved(inputs: Inputs, assumptions: Assumptions) -> float:
    """
    Hours saved from workflow efficiency (based on % time saved for A/R FTEs).
    """
    total_hours = (
        inputs.ar_headcount
        * assumptions.hours_per_fte_per_year
        * assumptions.productivity_time_saved_pct * assumptions.percentage_of_time_on_invoices
    )
    return max(0.0, total_hours)


def compute_bad_debt_savings(inputs: Inputs, assumptions: Assumptions) -> float:
    """
    Reduction in bad debt (relative % improvement applied to baseline bad debt).
    Baseline bad debt is modeled as a % of A/R balance, where:
    A/R ≈ annual_revenue × (current_dso_days / working_days_per_year).
    """
    estimated_ar_balance = inputs.annual_revenue * (
        inputs.current_dso_days / assumptions.working_days_per_year
    )
    baseline_bad_debt = estimated_ar_balance * inputs.bad_debt_pct
    savings = baseline_bad_debt * assumptions.bad_debt_reduction_relative_pct
    return max(0.0, savings)


def compute_roi_pct(total_benefit_usd: float, annual_price_usd: float) -> float:
    """
    ROI percentage: ((benefit - cost) / cost) * 100
    Returns +inf if cost <= 0 and benefit > 0.
    """
    if annual_price_usd <= 0:
        return float("inf") if total_benefit_usd > 0 else 0.0
    roi_ratio = (total_benefit_usd - annual_price_usd) / annual_price_usd
    return roi_ratio * 100.0


# -----------------------------
# Public API
# -----------------------------
def calculate_all(inputs: Inputs, assumptions: Assumptions) -> Results:
    """
    Compute all Daylit X ROI metrics from inputs and assumptions,
    automatically determining tier and price from ARR.
    """
    tier, annual_price = determine_tier_and_price(inputs.annual_revenue)

    cash_flow_improvement = compute_cash_flow_improvement(inputs, assumptions)
    employee_savings = compute_annualized_employee_savings(inputs, assumptions)
    productivity_hours_saved = compute_productivity_hours_saved(inputs, assumptions)
    bad_debt_savings = compute_bad_debt_savings(inputs, assumptions)

    # ROI excludes freed cash; we report cash flow separately for transparency
    total_benefit = employee_savings + bad_debt_savings
    roi_pct = compute_roi_pct(total_benefit, annual_price)

    # Opportunity cost: what client loses by not capturing these savings (could be earning this)
    opportunity_cost = total_benefit * assumptions.cost_of_capital_annual_pct

    return Results(
        roi_pct=roi_pct,
        cash_flow_improvement_usd=cash_flow_improvement,
        annualized_employee_savings_usd=employee_savings,
        productivity_hours_saved=productivity_hours_saved,
        bad_debt_savings_usd=bad_debt_savings,
        opportunity_cost_usd=opportunity_cost,
        tier=tier,
        annual_price_usd=annual_price,
    )


# -----------------------------
# CLI/demo usage
# -----------------------------
def _format_currency(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _format_number(value: float) -> str:
    return f"{value:,.0f}"


if __name__ == "__main__":
    # Show available industries
    print("Available industries with benchmark data:")
    for industry in get_available_industries():
        benchmark = get_industry_benchmark_dso(industry)
        print(f"  - {industry}: {benchmark} days DSO")
    print()

    # Example demonstration — using an industry with benchmark data
    selected_industry = "Hospitals/Healthcare Facilities"
    benchmark_dso = get_industry_benchmark_dso(selected_industry)
    client_dso = 65.0  # Client's actual DSO

    sample_inputs = Inputs(
        industry=selected_industry,
        annual_revenue=1200000,   # ARR
        ar_headcount=3,
        current_dso_days=client_dso,
        monthly_invoices=5000,
        fte_salary_base=80_000.0,
        bad_debt_pct=0.05,
    )
    sample_assumptions = Assumptions()

    results = calculate_all(sample_inputs, sample_assumptions)

    print("Daylit X ROI Summary")
    print("---------------------")
    print(f"Industry: {selected_industry}")
    if benchmark_dso:
        dso_diff = client_dso - benchmark_dso
        if dso_diff > 0:
            print(f"Your DSO ({client_dso:.0f} days) is {dso_diff:.0f} days ABOVE industry benchmark ({benchmark_dso} days)")
        elif dso_diff < 0:
            print(f"Your DSO ({client_dso:.0f} days) is {abs(dso_diff):.0f} days BELOW industry benchmark ({benchmark_dso} days)")
        else:
            print(f"Your DSO ({client_dso:.0f} days) matches industry benchmark ({benchmark_dso} days)")
    print()
    print(f"Tier: {results.tier}")
    print(f"Price (annual): {_format_currency(results.annual_price_usd)}")
    print(f"ROI: {results.roi_pct:,.1f}%")
    print(f"Cash flow improvement (freed cash): {_format_currency(results.cash_flow_improvement_usd)}")
    print(f"Employee savings (annualized): {_format_currency(results.annualized_employee_savings_usd)}")
    print(f"Productivity hours saved (annual): {_format_number(results.productivity_hours_saved)} hours")
    print(f"Bad debt savings (annual): {_format_currency(results.bad_debt_savings_usd)}")
    print(f"Opportunity cost (annual): {_format_currency(results.opportunity_cost_usd)}")


