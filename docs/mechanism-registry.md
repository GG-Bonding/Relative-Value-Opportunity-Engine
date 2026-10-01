# Mechanism registry

Each factor is registered before it is estimated. The registry is `mechanisms.registry.MECHANISMS`. A fit does not add a feature that is not on this list.

| Id | Kind | Statement | Expected sign on log EURGBP |
| --- | --- | --- | --- |
| rate_differential | STRUCTURAL_MECHANISM | UK 2Y minus DE 2Y changes the carry of sterling assets and the demand for GBP. | -1 |
| policy_differential | STRUCTURAL_MECHANISM | The traded object is the expected BoE path minus the expected ECB path, not the spot policy rate. | -1 |
| growth_differential | STRUCTURAL_MECHANISM | A UK growth surprise relative to the euro area supports GBP. | -1 |
| inflation_differential | STRUCTURAL_MECHANISM | Relative inflation pressure matters through the policy path it implies. | -1 |
| risk_premium_differential | STRUCTURAL_MECHANISM | A wider euro sovereign or credit premium versus the UK supports GBP. | -1 |
| positioning | PATTERN | Carry, speculative positioning, and options can amplify a move. They are not a structural fair-value input in V1. | none |

`assess_mechanisms` separates three claims:

- `STRUCTURAL_MECHANISM`: the economic statement is still the one that was registered.
- `PATTERN`: a statistical regularity, including positioning, that is not allowed to set fair value.
- `TRADABLE_ALPHA`: the mechanism's sign is in the current fit, the fit clears the R² and stability floors, and the alpha lifecycle is `ACTIVE`.

A mechanism can remain structural after the tradable lag has disappeared. That is an explicit output, not a failure of the registry.
