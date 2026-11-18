import {api} from './api.js';
export const store={user:null,productions:[],performances:[],calls:[],staff:[],assignments:[],debriefs:[],zones:[],posts:[],incidents:[],items:[],itemClaims:[],methods:[],methodDefault:null,evaluationData:null,productionId:null};
export async function refresh(){
  const base=await Promise.all([api('/productions'),api('/performances'),api('/crew-calls'),api('/staff'),api('/crew-assignments'),api('/show-debriefs'),api('/entrance-zones'),api('/zone-posts'),api('/house-incidents'),api('/lost-items'),api('/item-claims'),api('/forecast-methods')]);
  [store.productions,store.performances,store.calls,store.staff,store.assignments,store.debriefs,store.zones,store.posts,store.incidents,store.items,store.itemClaims]=base.slice(0,11);
  const methodCatalog=base[11];store.methods=methodCatalog.methods;store.methodDefault=methodCatalog.default_key;
  if(!store.productionId&&store.productions.length)store.productionId=store.productions[0].id;
  if(store.productionId)store.evaluationData=await api('/productions/'+store.productionId+'/method-evaluations');
  return store}
export const production=s=>s.productions.find(x=>x.id===Number(s.productionId));
export const performances=s=>s.performances.filter(x=>x.production_id===Number(s.productionId));
