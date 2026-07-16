# Generator Fleet Revenue Management — Linear Programming Model
#
# Optimises weekly acceptance decisions for a fixed rental fleet across four
# rental durations (1/4/8/16 weeks), maximising total revenue subject to
# demand and rolling capacity constraints. See README.md for the full write-up.

import os
import numpy as np
from pyomo.environ import *
import pandas as pd

# load data from excel (run from repo root, or this path resolves relative to this file)
_here = os.path.dirname(os.path.abspath(__file__))
file_name = os.path.join(_here, '..', 'data', 'generator_rental_data.xlsx')

df = pd.read_excel(file_name, 'Data', skiprows=9, header=None)
 
# drop only fully blank rows, keep all 52 weeks including holidays
df = df[df[1].notna()].reset_index(drop=True)
 
# holiday weeks have "CLOSED FOR HOLIDAY" in price column - becomes 0
cols_to_fill = [c for c in df.columns if c not in [0, 1, 2]]
df[cols_to_fill] = df[cols_to_fill].apply(pd.to_numeric, errors='coerce').fillna(0)

# convert to numpy arrays
# columns: price, demand, actual acceptances, returns for each duration (1,4,8,16 wk)
price_np   = df[[4,8,12,16]].to_numpy(dtype=float)
demand_np  = df[[5,9,13,17]].to_numpy(dtype=float)
actual_np  = df[[6,10,14,18]].to_numpy(dtype=float)
returns_np = df[[7,11,15,19]].to_numpy(dtype=float)
inv_np     = df[2].to_numpy(dtype=float)
 
numofWeeks     = len(price_np)      # 52 weeks
numofDurations = 4                  # 1-wk, 4-wk, 8-wk, 16-wk
 
# parameters
total_fleet = 300
unit_cost   = 3000
durations   = [1, 4, 8, 16]    # in weeks
dur_days    = [7, 28, 56, 112]  # in days
 
# compute exogenous returns (generators returning from pre-2025 rentals)
# these are fixed - not part of our decisions
exo_returns = np.zeros(numofWeeks)
for t in range(numofWeeks):
    total_ret = returns_np[t].sum()
    within_ret = 0
    for d in range(numofDurations):
        prev = t - durations[d]
        if prev >= 0:
            within_ret += actual_np[prev][d]
    exo_returns[t] = total_ret - within_ret
 
# build capacity RHS for each week
# rhs[t] = max generators that can be active from within-horizon decisions at week t
rhs = np.zeros(numofWeeks)
rhs[0] = inv_np[0]
for t in range(1, numofWeeks):
    rhs[t] = rhs[t-1] + exo_returns[t]
 
# build pyomo model
model = ConcreteModel()
 
# decision variables: x[t,d] = number of generators accepted in week t for duration d
model.x = Var(range(numofWeeks), range(numofDurations), domain=NonNegativeReals)
 
# objective function - maximise total revenue
def model_objective(model):
    return sum(dur_days[d] * price_np[t][d] * model.x[t,d]
               for t in range(numofWeeks) for d in range(numofDurations))
 
model.obj = Objective(rule=model_objective, sense=maximize)
 
# constraint 1 - cannot accept more than demand
def demand_rule(model, t, d):
    return model.x[t,d] <= demand_np[t][d]
 
model.cost_demand = Constraint(range(numofWeeks), range(numofDurations), rule=demand_rule)
 
# constraint 2 - capacity constraint (rolling window)
# total active generators at week t cannot exceed available inventory rhs[t]
# a rental accepted at week t-k for duration d is still active if k < d
def cap_rule(model, t):
    return (sum(model.x[t-k, d]
                for d in range(numofDurations)
                for k in range(durations[d])
                if t-k >= 0) <= rhs[t])
 
model.cost_cap = Constraint(range(numofWeeks), rule=cap_rule)
 
# solve
solver  = SolverFactory('glpk')
results = solver.solve(model)
 
# results
if (results.solver.status == SolverStatus.ok) and \
   (results.solver.termination_condition == TerminationCondition.optimal):
 
    print('optimal solution found')
 
    # optimised revenue
    opt_revenue = model.obj()
 
    # actual revenue from dataset
    actual_revenue = sum(actual_np[t][d] * price_np[t][d] * dur_days[d]
                         for t in range(numofWeeks) for d in range(numofDurations))
 
    # rebuild optimised inventory week by week for load factor calculation
    opt_x = np.array([[model.x[t,d]() for d in range(numofDurations)]
                       for t in range(numofWeeks)])
 
    opt_inv = np.zeros(numofWeeks)
    opt_inv[0] = inv_np[0]
    for t in range(1, numofWeeks):
        endo_ret = sum(opt_x[t - durations[d]][d] if t - durations[d] >= 0 else 0
                       for d in range(numofDurations))
        opt_inv[t] = opt_inv[t-1] - opt_x[t-1].sum() + exo_returns[t] + endo_ret
 
    # load factor = (fleet - average available inventory) / fleet
    actual_load_factor = (total_fleet - np.mean(inv_np)) / total_fleet
    opt_load_factor    = (total_fleet - np.mean(opt_inv)) / total_fleet
 
    # ROI = (total revenue / fleet size) / unit cost
    actual_roi = (actual_revenue / total_fleet) / unit_cost
    opt_roi    = (opt_revenue    / total_fleet) / unit_cost
 
    # print results
    print('actual revenue: ',    round(actual_revenue, 2))
    print('optimised revenue: ', round(opt_revenue, 2))
    print('actual load factor: ',    round(actual_load_factor, 4))
    print('optimised load factor: ', round(opt_load_factor, 4))
    print('actual ROI: ',    round(actual_roi, 4))
    print('optimised ROI: ', round(opt_roi, 4))
    print('revenue improvement: ',    round(opt_revenue - actual_revenue, 2))
    print('percentage improvement: ', round((opt_revenue / actual_revenue - 1) * 100, 2), '%')
    
    # summary by rental length
    print('\nRental Length Summary:')
    for di, d in enumerate([1, 4, 8, 16]):
        total_demand    = int(demand_np[:, di].sum())
        total_actual    = int(actual_np[:, di].sum())
        total_optimised = int(sum(round(model.x[t, di]()) for t in range(numofWeeks)))
        print(f'{d}-week: demand={total_demand}, actual={total_actual}, optimised={total_optimised}')
 
    # print weekly optimal decisions
    print('\nweek  1-wk  4-wk  8-wk  16-wk')
    for t in range(numofWeeks):
        print('week', t+1, ':',
              round(model.x[t,0](), 1),
              round(model.x[t,1](), 1),
              round(model.x[t,2](), 1),
              round(model.x[t,3](), 1))
 
else:
    print('solver did not find optimal solution')
    print('status: ', results.solver.status)
    print('termination condition: ', results.solver.termination_condition)