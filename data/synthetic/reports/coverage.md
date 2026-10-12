# Coverage grid

Share of valid cells with at least one event, per split and projection.

| Projection | train | dev | test |
|---|---|---|---|
| kind x rule x verdict | 54/66 (81.8%) | 47/66 (71.2%) | 51/66 (77.3%) |
| kind x verification x verdict | 23/24 (95.8%) | 19/24 (79.2%) | 20/24 (83.3%) |
| kind x position | 17/20 (85.0%) | 17/20 (85.0%) | 17/20 (85.0%) |
| language x verdict | 17/18 (94.4%) | 9/18 (50.0%) | 9/18 (50.0%) |
| attack style x verdict | 7/21 (33.3%) | 5/21 (23.8%) | 5/21 (23.8%) |
| kind x tool outcome | 4/6 (66.7%) | 4/6 (66.7%) | 4/6 (66.7%) |

**train: empty primary cells (12)**: `model_output|R1|policy_silent`, `tool_call|R1|policy_silent`, `tool_call|R2|violation`, `model_output|R3|policy_silent`, `tool_call|R3|allow`, `tool_call|R3|policy_silent`, `tool_response|R3|policy_silent`, `tool_call|R5|allow`, `tool_call|R6|violation`, `tool_call|R11|violation`, `tool_call|R11|policy_silent`, `tool_response|R12|policy_silent`

**dev: empty primary cells (19)**: `model_output|R1|policy_silent`, `tool_call|R1|policy_silent`, `model_output|R2|violation`, `tool_call|R2|violation`, `model_output|R3|policy_silent`, `tool_call|R3|allow`, `tool_call|R3|policy_silent`, `tool_response|R3|policy_silent`, `model_output|R4|policy_silent`, `tool_response|R4|policy_silent`, `tool_call|R5|allow`, `tool_call|R6|allow`, `tool_call|R6|violation`, `tool_call|R7|policy_silent`, `tool_call|R8|policy_silent`, `model_output|R11|policy_silent`, `tool_call|R11|violation`, `tool_call|R11|policy_silent`, `tool_response|R12|policy_silent`

**test: empty primary cells (15)**: `model_output|R1|policy_silent`, `tool_call|R1|policy_silent`, `model_output|R2|violation`, `tool_call|R2|violation`, `model_output|R3|policy_silent`, `tool_call|R3|policy_silent`, `tool_response|R3|policy_silent`, `tool_call|R4|allow`, `tool_response|R4|policy_silent`, `tool_call|R6|allow`, `tool_call|R6|violation`, `model_output|R10|policy_silent`, `tool_call|R11|violation`, `tool_call|R11|policy_silent`, `tool_response|R12|policy_silent`
