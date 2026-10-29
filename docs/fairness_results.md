# ShareSafe Fairness Engine Simulation Report
> **Data Source**: Synthetic Population Simulation (Seed=42)  
> **Simulation Parameters**: 40 synthetic student users across 4 teams, 3 equipment resources, 4 weeks (28 days) traffic.  
> **Integrity Notice**: These numbers are purely measured from reproducible synthetic simulations. No claims of measured real-world accuracy or adoption are made.

---

## Comparative Performance: Naive FCFS vs ShareSafe Engine

| Metric | Naive FCFS | ShareSafe Engine | Absolute Delta | Relative Change |
|---|---|---|---|---|
| **Borrowers Receiving Access** (≥1 booking) | 36 / 40 (90.0%) | **35 / 40 (87.5%)** | -1 users | -2.5% |
| **Top 10% User Hour Share** (Monopoly risk) | 45.6% | **26.5%** | -19.1% | **Reduced concentration** |
| **Gini Coefficient** (0=equality, 1=monopoly) | 0.5965 | **0.4676** | -0.1289 | **Inequality reduced** |
| **Total Allocated Equipment Hours** | 629.0h | 431.0h | -198.0h | Regulated by usage caps |
| **Approved Bookings** | 256 | 183 | -73 | Regulated |

---

## Key Findings from Simulation
1. **Inequality & Monopoly Reduction**: Under naive FCFS, heavy early-booking users monopolized prime recurring slots, with the top 10% capturing **45.6%** of all equipment hours. The ShareSafe fairness engine curtailed this to **26.5%** (a 19.1 percentage-point drop in concentration).
2. **Gini Coefficient Improvement**: The overall distribution inequality dropped significantly from **0.5965** down to **0.4676** due to rolling usage caps and priority weighting.
3. **Reproducibility**: Run `python scripts/simulate_fairness.py` to reproduce these exact results.
