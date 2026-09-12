// _dev/_boot_test.js — 启动链路无头集成测试（最小 DOM 桩 + 真实地图 + 真实推送帧样本）
// 目的：本次前端改动动了启动顺序（/api/config → 地图 → SSE）与渲染入口，
//       语法检查查不出「运行时才炸」的问题。本测试把 index.html 的内联脚本丢进 vm 真跑一遍：
//       配置下发 → 地图下拉填充 → 载图 → 建 SSE → 喂真实 status 帧 → 跑两帧渲染，全程不许抛异常。
//
// 用法: node _boot_test.js [index.html路径，默认 <项目根>/index.html]
const fs=require('fs'),path=require('path'),vm=require('vm');
const ROOT=path.join(__dirname,'..');
const SRC=process.argv[2]||path.join(ROOT,'index.html');
const js=fs.readFileSync(SRC,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

let bad=0;const ok=(c,m)=>{if(!c){bad++;console.log('FAIL',m);}};
const noop=()=>{};
const ctxProxy=new Proxy({measureText:()=>({width:40}),canvas:{width:1200,height:800}},
  {get:(t,k)=>(k in t?t[k]:noop),set:(t,k,v)=>(t[k]=v,true)});

const els={};
function el(id){
  if(els[id])return els[id];
  const e={id,style:{},textContent:'',innerHTML:'',onclick:null,disabled:false,
    clientWidth:1200,clientHeight:800,width:0,height:0,
    getContext:()=>ctxProxy,setPointerCapture:noop,addEventListener:noop,
    querySelectorAll:()=>[],appendChild:noop};
  Object.defineProperty(e,'innerHTML',{get(){return this._h||''},set(v){this._h=v;}});
  return els[id]=e;
}
const fetched=[],esInstances=[],rafQueue=[],timers=[];
const CONFIG={maps:["EE","BB","CC","DD"],mapNames:{EE:"二厂1楼",BB:"二厂3楼",CC:"二厂2楼",DD:"钻房1楼"},
              defaultMap:"BB",pollGuessMs:250};
const BB=JSON.parse(fs.readFileSync(path.join(ROOT,'maps','BB.json'),'utf8'));
function fakeFetch(url){
  fetched.push(url);
  if(url==='api/config')return Promise.resolve({ok:true,json:()=>Promise.resolve(CONFIG)});
  if(url==='maps/BB.json')return Promise.resolve({ok:true,json:()=>Promise.resolve(BB)});
  return Promise.resolve({ok:true,json:()=>Promise.resolve({ok:{},err:null})});
}
class FakeES{constructor(u){esInstances.push(this);this.url=u;}close(){}}
const sandbox={
  console,JSON,Math,Date,Promise,Object,Array,Number,String,Boolean,isFinite,isNaN,parseInt,parseFloat,
  RegExp,Error,TypeError,Map,Set,Proxy,URLSearchParams,TextEncoder,
  fetch:fakeFetch,EventSource:FakeES,
  requestAnimationFrame:cb=>{rafQueue.push(cb);return rafQueue.length;},
  setInterval:(fn,ms)=>{timers.push([fn,ms]);return timers.length;},
  setTimeout:(fn,ms)=>{timers.push([fn,ms]);return timers.length;},
  performance:{now:()=>0},
  addEventListener:noop,removeEventListener:noop,
  location:{search:'',href:'http://127.0.0.1:8899/'},
  document:{getElementById:el,querySelectorAll:()=>[],
            addEventListener:noop,body:el('body'),documentElement:el('html')},
  devicePixelRatio:1,
};
sandbox.window=sandbox;sandbox.globalThis=sandbox;
sandbox.CanvasRenderingContext2D={prototype:{roundRect:noop}};   // 让 rr() 走原生分支

const ctx=vm.createContext(sandbox);
try{ vm.runInContext(js,ctx,{filename:'index.html<inline>'}); }
catch(e){ ok(false,'脚本执行抛异常: '+e.message); process.exit(1); }

(async()=>{
  // 等启动链跑完（fetch 的 continuation 全是微任务，用 setImmediate 兜底冲洗）
  for(let i=0;i<50&&!(el('mapSel')._h||'').includes('value=');i++)
    await new Promise(r=>setImmediate(r));
  ok(fetched[0]==='api/config',`启动第一步必须是取配置（实际 ${fetched[0]}）`);
  const sel=el('mapSel');
  ok((sel._h||'').includes('EE')&&(sel._h||'').includes('BB'),
     '地图下拉必须由配置填充（含 EE/BB）：'+(sel._h||'(空)').slice(0,60));
  ok((sel._h||'').includes('value="BB" selected'),'默认图必须是配置里的 BB');
  ok(fetched.includes('maps/BB.json'),'必须按配置的默认图载入地图 JSON（实际 '+fetched.join(',')+'）');
  ok(esInstances.length===1&&esInstances[0].url==='/api/events',
     '载图成功后必须建立 SSE 连接（实际 '+esInstances.length+'）');

  // ---- 喂一条真实推送状态帧（取自现场快照样本），再跑两帧渲染 ----
  const snap=JSON.parse(fs.readFileSync(path.join(ROOT,'snap_tmp.json'),'utf8'));
  const row=Object.assign({},snap.data.find(r=>r.mapCode==='BB')||snap.data[0],{mapCode:'BB'});
  let err=null;
  try{ esInstances[0].onmessage({data:JSON.stringify({e:'status',a:row})}); }catch(e){ err=e; }
  ok(!err,'处理真实 status 帧不得抛异常：'+(err&&err.stack||''));
  let frames=0,ferr=null;
  const listRows=()=>((el('list')._h||'').match(/data-id/g)||[]).length;
  try{ while(rafQueue.length&&frames<4){const cb=rafQueue.shift();cb(frames++*16);} }
  catch(e){ ferr=e; }
  ok(!ferr,'渲染帧不得抛异常：'+(ferr&&ferr.stack||''));
  ok(frames>=1,'应当至少渲染一帧（实际 '+frames+'）');
  timers.filter(([,ms])=>ms===1000).forEach(([fn])=>fn());   // 手动跑一次 1s UI 节拍(feedTick)
  ok(listRows()>=1,'真实 status 帧经 UI 节拍后应进入车辆列表（实际 '+listRows()+' 条）');
  ok((el('lastOk').textContent||'').includes('更新于'),'顶栏刷新时间应被写入');
  // ---- 后台挂起后恢复：至少一帧不得抛异常（渲染 + 收敛路径）----
  let rerr=null;
  try{ const nq=rafQueue.slice();rafQueue.length=0;
       if(nq.length)nq[0](60000);                        // 凭空跳 60s：等同后台挂起后恢复
       while(rafQueue.length&&frames<8){const cb=rafQueue.shift();cb(60000+frames*16);frames++;}
  }catch(e){ rerr=e; }
  ok(!rerr,'挂起 60s 后恢复渲染不得抛异常：'+(rerr&&rerr.stack||''));
  console.log('启动链: '+fetched.join(' → ')+' ；渲染 '+frames+' 帧无异常（含挂起恢复帧）；车辆列表条目 '+listRows());
  console.log(bad===0?'BOOT PASS：配置下发→地图→SSE→首帧渲染 全链路无异常'
                     :`BOOT FAIL ${bad}`);
  process.exit(bad?1:0);
})();
