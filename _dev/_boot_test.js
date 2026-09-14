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
// api/pods 桩：优先用现场拉回的真实固件（_dev/rest_out），否则退到 2 条样例。
// 译码这里只是「备料」（mapDataCode 前7位x毫米/后7位y毫米），不复制任何业务断言——
// 译码正确性由 _pods_test.py 用同一批固件单独把关。
function loadPodsPayload(){
  try{
    const rows=JSON.parse(fs.readFileSync(path.join(ROOT,'_dev','rest_out','podBerthMat_BB.json'),'utf8')).data;
    const pods=[];
    for(const r of rows){
      const c=r.mapDataCode||'';
      if(!/^\d{7}[A-Z]{2}\d{7}$/.test(c))continue;
      pods.push({code:r.podCode,x:+(c.slice(0,7))/1000,y:+(c.slice(9,16))/1000,
                 pal:(r.podCode||'').startsWith('P'),area:r.areaCode||'',pos:r.positionCode||''});
    }
    if(pods.length)return{ts:1,pods:{BB:pods},err:{}};
  }catch(e){/* 固件缺失走样例 */}
  return {ts:1,pods:{BB:[{code:'L00278',x:4.771,y:52.930,pal:false,area:'zfh',pos:'004771BB052930'},
                          {code:'P00281',x:5.188,y:39.330,pal:true,area:'',pos:''}]},err:{}};
}
const PODS_PAYLOAD=loadPodsPayload();
function fakeFetch(url){
  fetched.push(url);
  if(url==='api/config')return Promise.resolve({ok:true,json:()=>Promise.resolve(CONFIG)});
  const mm=/^maps\/([A-Z]+)\.json$/.exec(url);            // 任意地图都从磁盘读真实文件
  if(mm)return Promise.resolve({ok:true,json:()=>Promise.resolve(
    JSON.parse(fs.readFileSync(path.join(ROOT,'maps',mm[1]+'.json'),'utf8')))});
  if(url==='api/pods')return Promise.resolve({ok:true,json:()=>Promise.resolve(PODS_PAYLOAD)});
  if(/^maps\/rets\.json/.test(url)){                     // 背景区域：读真实产物，缺文件则给空表
    try{return Promise.resolve({ok:true,json:()=>Promise.resolve(
      JSON.parse(fs.readFileSync(path.join(ROOT,'maps','rets.json'),'utf8')))});}
    catch(e){return Promise.resolve({ok:true,json:()=>Promise.resolve({ts:0,maps:{}})});}
  }
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
  clearTimeout:noop,clearInterval:noop,
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
  ok(fetched.includes('api/pods'),'启动链必须拉取货架清单（实际 '+fetched.join(',')+'）');
  ok((el('legend')._h||'').includes('货架'),'货架清单就位后图例必须出现「货架」计数（实际 '+(el('legend')._h||'').slice(-80)+'）');
  ok(esInstances.length===1&&esInstances[0].url==='/api/events?map=BB',
     '载图成功后必须建立「按图分组」的 SSE 订阅（实际 '+(esInstances[0]||{}).url+'）');
  let oerr=null;                                   // 握手成功路径：状态位应变「推送」且不抛异常
  try{ esInstances[0].onopen&&esInstances[0].onopen({}); }catch(e){ oerr=e; }
  ok(!oerr&&(el('src').textContent||'')==='推送','SSE 握手成功后状态位应显示「推送」（实际「'+el('src').textContent+'」）'+
     (oerr?' 异常：'+oerr.message:''));

  // ---- 换图：订阅跟着换（分组订阅），且地图走内存缓存不再重复拉 ----
  const bbFetches=()=>fetched.filter(u=>u==='maps/BB.json').length;
  await ctx.switchMap('CC');
  ok(esInstances.length===2&&esInstances[1].url==='/api/events?map=CC',
     '换图后应重新订阅新图（实际 '+(esInstances[1]||{}).url+'）');
  await ctx.switchMap('BB');
  ok(esInstances.length===3&&esInstances[2].url==='/api/events?map=BB','换回原图应再订一次 BB');
  ok(bbFetches()===1,'地图内存缓存生效：来回切图不应重复拉 maps/BB.json（实际 '+bbFetches()+' 次）');
  ok(fetched.some(u=>/^maps\/rets\.json/.test(u)),'启动链必须拉取地图背景 maps/rets.json（实际 '+fetched.join(',')+'）');
  // RETS 是 index.html 里的 let 声明，不挂到 sandbox 上，只能在 vm 上下文里求值。
  // fetch 是异步的，loadRets() 要等微任务冲洗完才落值——前面等启动链的循环已冲到 mapSel 填充，
  // 这里再补一轮，确保 loadRets 的 continuation 已经跑过。
  for(let i=0;i<10;i++) await new Promise(r=>setImmediate(r));
  const RETS=vm.runInContext('typeof RETS==="undefined"?null:RETS',ctx);
  ok(RETS&&typeof RETS==='object','背景数据必须落到 RETS（实际 '+typeof RETS+'）');
  ok(RETS&&RETS.BB&&Array.isArray(RETS.BB.polys),
     '背景必须按图码分图存放（BB 缺 polys：'+(RETS?Object.keys(RETS).join('/'):'RETS 为空')+'）');
  ok(!RETS||Object.keys(RETS).every(k=>Array.isArray(RETS[k].labels)),
     '每张图的背景都必须带 labels 数组（渲染按 ret.labels 遍历，缺了会抛异常）');
  ok(!RETS||Object.keys(RETS).every(k=>RETS[k].areas&&typeof RETS[k].areas==='object'),
     '每张图的背景都必须带 areas 字典（悬停提示靠它把 areaCode 译成中文名；缺了会退化成显示原始码）');
  // 背景/区域名是低频数据：启动只读本机静态文件，绝不允许触发任何服务端"联系 RCS"的端点。
  // （syncMaps/syncPods 会登录 CMS，连点还会触发 30s 节流——启动绝不该碰。）
  const rcsHits=fetched.filter(u=>/^api\/(syncMaps|syncPods)/.test(u));
  ok(!rcsHits.length,'启动链不得调用会联系 RCS 的端点（实际 '+rcsHits.join(',')+'）');
  ok(fetched.filter(u=>/^maps\/rets\.json/.test(u)).length===1,
     '背景只在启动时读一次本机静态文件（实际 '+fetched.filter(u=>/^maps\/rets\.json/.test(u)).length+' 次）');

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

  // ---- SSE pods 事件：服务端增量维护后的货架表广播 → 图例更新 + 渲染不炸 ----
  let perr=null;
  try{
    esInstances[0].onmessage({data:JSON.stringify({e:'pods',ts:2,
      pods:{BB:[{code:'L90001',x:4.771,y:52.930,pal:false,area:'zfh',pos:'004771BB052930'},
                {code:'P90002',x:5.188,y:39.330,pal:true,area:'',pos:''}]},err:{}})});
  }catch(e){ perr=e; }
  ok(!perr,'处理 pods 事件不得抛异常：'+(perr&&perr.stack||''));
  ok((el('legend')._h||'').includes('货架 2'),'pods 事件后图例计数应更新为 2（实际 '+(el('legend')._h||'').slice(-60)+'）');
  try{ let n2=0; while(rafQueue.length&&n2<2){const cb=rafQueue.shift();cb(100000+n2*16);n2++;} }
  catch(e){ ok(false,'pods 更新后渲染不得抛异常：'+(e&&e.stack||'')); }

  // ---- 搜索：储位号 / 货架号（跨图定位、车载中、认不出时的提示）----
  const msgTxt=()=>el('msg').textContent||'';
  async function trySearch(v){
    el('q').value=v; let e2=null;
    try{ await ctx.doSearch(); }catch(err){ e2=err; }
    return e2;
  }
  let e3=await trySearch('004771BB051930');                     // 储位号（BB 图）
  ok(!e3,'搜储位号不得抛异常：'+(e3&&e3.stack||''));
  ok(msgTxt().includes('储位'),'搜储位号应有结果提示（实际「'+msgTxt()+'」）');
  e3=await trySearch('L90001');                                 // 货架号（在储位上）
  ok(!e3&&msgTxt().includes('L90001'),'搜货架号应命中并提示（实际「'+msgTxt()+'」）');
  e3=await trySearch('L00000');                                 // 不存在的货架号
  ok(!e3&&msgTxt().includes('没找到'),'搜不存在的货架应给出提示（实际「'+msgTxt()+'」）');
  e3=await trySearch('乱七八糟');                                // 认不出的输入
  ok(!e3&&msgTxt().includes('认不出'),'认不出的输入应给出提示（实际「'+msgTxt()+'」）');

  console.log('启动链: '+fetched.join(' → ')+' ；渲染 '+frames+' 帧无异常（含挂起恢复帧）；车辆列表条目 '+listRows());
  console.log(bad===0?'BOOT PASS：配置下发→地图→SSE→首帧渲染 全链路无异常'
                     :`BOOT FAIL ${bad}`);
  process.exit(bad?1:0);
})();
