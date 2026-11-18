export const ROLES={admin:'值班经理',steward:'场务领班',viewer:'只读观察员'};
export const STATES={observed:'已验票',scheduled:'待预测',forecasted:'待编班',crew_confirmed:'点名已确认',proposed:'待确认',confirmed:'已确认',invalidated:'证据失效'};

export const DEBRIEF_STATES={draft:'待经理归档',archived:'已归档冻结'};

export const INCIDENT_STATES={open:'待分派',assigned:'处置中',resolved:'待经理复核',closed:'已关闭'};

export const EVAL_STATES={queued:'排队中',running:'回测进行中',completed:'三周已完成',failed:'中断待恢复'};
export const EVAL_WEEK_STATES={pending:'待计算',done:'已完成',failed:'计算失败'};
export const EVAL_STALE_REASONS={'baseline-method-changed':'现用方案已变化','arrivals-changed':'评估后验票已更正'};
