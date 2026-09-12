// _dev/_resume_test.js — 「浏览器后台切回前台，动画追赶/补帧导致 AGV 瞬间位移(飞)」回归
//
// 复现条件：浏览器把后台标签的 requestAnimationFrame 挂起/节流，但 SSE 仍在投递事件，
//           ingest 持续把 sEnd 推到最新上报位置；回前台时 s 与 sEnd 之间积压了几十米，
//           旧 frame() 照常推进 → 拿积压进度做「追帧」，AGV 沿轨迹加速冲刺 = "飞"。
//
// 本测试从 index.html 实时抽取 stepAgv / resyncAgv / ingest / setRoute 等函数（防止测试与实现脱钩），
// 并用一份「修复前 frame() 内联逻辑」的原样转录做对照，断言修复后的行为。
//
// 用法: node _resume_test.js [index.html路径，默认 <项目根>/index.html]
const fs=require('fs'),path=require('path');
const SRC=process.argv[2]||path.join(__dirname,'..','index.html');
const js=fs.readFileSync(SRC,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

// ---- 虚拟时钟：让 sim 与实现内部的 Date.now() 同源，避免真实时间把追帧窗口算歪 ----
let clock=Date.now();
Date.now=()=>clock;

// ---- 直线走廊地图：节点每 1m 一个，共 400m（引擎的路网贴靠/投影逻辑照常生效）----
const N=400;
global.map={nodes:[],edges:[]};
for(let i=0;i<=N;i++){map.nodes.push([i+1,i,0,16]);if(i)map.edges.push([i,i+1]);}
map.nodeMap={};map.nodes.forEach(n=>map.nodeMap[n[0]]=n);
map.adj={};map.edges.forEach(([a,b])=>{(map.adj[a]??=[]).push(b);(map.adj[b]??=[]).push(a);});

// ---- 从源码取常量与函数（数值全部读自 index.html，改源即改测试）----
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
let POLL_GUESS=parseFloat(js.match(/let POLL_GUESS=(\d+)/)[1]);
const RESUME_GAP_S=parseFloat(js.match(/const RESUME_GAP_S=([\d.]+)/)[1]);
const SUSPEND_GAP_MS=parseFloat(js.match(/const SUSPEND_GAP_MS=(\d+)/)[1]);
const RESYNC_DIST=parseFloat(js.match(/const RESYNC_DIST=(\d+)/)[1]);
const ex=re=>{const m=js.match(re);if(!m)throw new Error('extract fail: '+re);return m[0];};
eval(ex(/function snapNode[\s\S]*?\n}/));
eval(ex(/const dot3=[^\n]*/).replace('const dot3','var dot3'));
eval(ex(/function graphRoute[\s\S]*?\n}/));
eval(ex(/function buildPoly[\s\S]*?\n}/));
eval(ex(/function posAt[\s\S]*?\n}/));
eval(ex(/function projLen[\s\S]*?\n}/));
eval(ex(/function resolveHead[\s\S]*?\n}/));
eval(ex(/function setRoute[\s\S]*?\n}/));
eval(ex(/function ingest[\s\S]*?\n}/));
eval(ex(/function resyncAgv[\s\S]*?\n}/));
eval(ex(/function stepAgv[\s\S]*?\n}/));

// ---- 修复前 frame() 内联逐车推进逻辑（原样转录，仅把 continue 换成 return）----
function oldStep(a,dt){
  if(!a.poly||a.poly.length<2){
    const e=Math.min(dt*2,1);a.x+=(a.tx-a.x)*e;a.y+=(a.ty-a.y)*e;
    if(!a.turn){const wa=-(+a.robotDir||0)*Math.PI/180;
      let da=wa-a.ang;while(da>Math.PI)da-=2*Math.PI;while(da<-Math.PI)da+=2*Math.PI;
      a.ang+=da*Math.min(dt*4,1);}
    a.v=0;return;
  }
  if(a.turn){
    a.turn.t-=dt;
    let da=a.turn.to-a.ang;while(da>Math.PI)da-=2*Math.PI;while(da<-Math.PI)da+=2*Math.PI;
    const step=Math.abs(da)<=dt*9?da:Math.sign(da)*dt*9;
    a.ang+=step;da-=step;
    if(a.turn.t<=0||Math.abs(da)<0.02){a.ang=a.turn.to;a.turn=null;}
    return;
  }
  let vT=a.sEnd>a.s+0.02?a.vCyc:0;
  if(vT>0&&a.pushT){const tl=(a._gap||POLL_GUESS)/1000*0.8-(Date.now()-a.pushT)/1000;
    if(tl<0.3&&a.sEnd-a.s>0.05)vT=Math.max(vT,(a.sEnd-a.s)/Math.max(tl,0.1));}
  vT=Math.min(vT,2);
  const nc=a.corners.find(c=>!c.hit&&c.s>a.s);
  if(nc&&nc.s-a.s<0.5)vT=Math.min(vT,Math.max(0.05,vT)*clamp((nc.s-a.s)/0.5,0,1)+nc.cap*(1-clamp((nc.s-a.s)/0.5,0,1)));
  const endD=a.sEnd-a.s;
  if(endD<0.5)vT=Math.min(vT,Math.max(0.03,endD/0.5*vT));
  const acc=(vT>a.v?1.2:1.8)*dt;
  a.v=Math.abs(vT-a.v)<=acc?vT:a.v+Math.sign(vT-a.v)*acc;
  a.s=Math.min(a.sEnd,a.s+a.v*dt);
  const c=a.corners.find(c=>!c.hit&&c.s<=a.s+1e-6);
  if(c){a.s=c.s;const p=posAt(a);a.x=p.x;a.y=p.y;a.v=0;c.hit=true;a.turn={t:0.35,to:c.to};return;}
  const p=posAt(a);a.x=p.x;a.y=p.y;
  const[th,rev]=resolveHead(p.ang,a.robotDir);
  a._rev=rev;
  let hda=th-a.ang;while(hda>Math.PI)hda-=2*Math.PI;while(hda<-Math.PI)hda+=2*Math.PI;
  const hw=dt*10;
  a.ang+=Math.abs(hda)<=hw?hda:Math.sign(hda)*hw;
}

// ---- 仿真脚手架 ----
const SPEED=1.0;                      // 真实车速 m/s
const agvs=new Map();
let trueX=0,lastReportX=0,lastT=0,steps=0;
const rep=x=>({tx:x,ty:0,pathPts:[[x,0],[x+60,0]],robotDir:"0",status:"2",_seen:clock});
function feed(){lastReportX=trueX;const a=rep(trueX);ingest(a,agvs.get("1"),clock);agvs.set("1",a);}
function frameNew(t){                 // 修复后：等同于 index.html 的 frame() 逐车调度
  const raw=(t-lastT)/1000;lastT=t;
  const dt=clamp(raw,0,0.25);
  const resumed=raw>RESUME_GAP_S;
  const now=Date.now();
  for(const a of agvs.values()){
    if(resumed||a.sEnd-a.s>RESYNC_DIST)resyncAgv(a,now);
    else stepAgv(a,dt);
  }
}
function frameOld(t){                 // 修复前：无挂起判别，直接推进
  const dt=Math.min((t-lastT)/1000,0.25);lastT=t;
  for(const a of agvs.values())oldStep(a,dt);
}
function drive(spanMs,render,tick){   // 推进虚拟时钟；render=是否出帧（后台=false）
  const end=clock+spanMs;
  while(clock<end){
    clock+=16;steps++;trueX+=SPEED*0.016;
    if(steps%13===0)feed();           // ≈208ms 一次上报（RCS 引擎实测节奏）
    if(render)tick(clock);
  }
}
function trail(ms,tick){              // 恢复后：冻结真值，只量动画自己走了多远
  const end=clock+ms;let d=0,prev=[agvs.get("1").x,agvs.get("1").y];
  while(clock<end){clock+=16;tick(clock);
    const a=agvs.get("1");d+=Math.hypot(a.x-prev[0],a.y-prev[1]);prev=[a.x,a.y];}
  return d;
}
function scenario(tick,hiddenMs){
  agvs.clear();trueX=0;steps=0;clock=Date.now();lastT=clock;
  feed();                             // 首帧落位
  drive(10000,true,tick);             // 前台正常跑 10s
  const before=agvs.get("1").x;
  drive(hiddenMs,false,tick);         // 后台挂起：rAF 停，SSE/ingest 照常
  const backlog=agvs.get("1").sEnd-agvs.get("1").s;
  const reported=lastReportX;         // 挂起期间最后一次上报位置（唯一可用的"真相证据"）
  tick(clock);                        // 恢复首帧
  const after1=agvs.get("1").x;
  const fly=trail(3000,tick);         // 恢复后 3s 动画自身位移 = "飞"的度量
  return {before,backlog,reported,after1,fly};
}
let bad=0;const ok=(c,m)=>{if(!c){bad++;console.log('FAIL',m);}};

console.log(`常量（读自 index.html）: RESUME_GAP_S=${RESUME_GAP_S}  SUSPEND_GAP_MS=${SUSPEND_GAP_MS}  RESYNC_DIST=${RESYNC_DIST}  POLL_GUESS=${POLL_GUESS}`);

for(const hidden of [40000,1200]){
  const O=scenario(frameOld,hidden);
  const Rn=scenario(frameNew,hidden);
  console.log(`\n[挂起 ${hidden/1000}s] 积压弧长 ${O.backlog.toFixed(1)}m`);
  console.log(`  修复前: 恢复首帧仍在旧位置(离最新上报 ${(O.after1-O.reported).toFixed(1)}m)，`
    +`随后 3s 沿轨迹自行补帧 ${O.fly.toFixed(2)}m → 观感"飞"`);
  console.log(`  修复后: 恢复首帧即落在最新上报位(误差 ${Math.abs(Rn.after1-Rn.reported).toExponential(1)}m)，`
    +`随后 3s 自行补帧 ${Rn.fly.toFixed(2)}m`);
  ok(O.fly>1,'修复前本就应当出现补帧冲刺（对照有效）');
  ok(Rn.fly<0.3,`修复后恢复阶段不应有补帧位移（实测 ${Rn.fly.toFixed(2)}m）`);
  ok(Math.abs(Rn.after1-Rn.reported)<1e-6,'修复后恢复首帧应收敛到最新上报位置');
  ok(Math.abs(O.after1-O.reported)>0.5,'修复前恢复首帧应仍停在上次渲染位置（对照有效）');
}

// ---- 稳态回归：无挂起时动画仍按真实车速平滑跟随（修复不得改变正常行为）----
function steady(tick){
  agvs.clear();trueX=0;steps=0;clock=Date.now();lastT=clock;feed();
  const x0=agvs.get('1').x;drive(6000,true,tick);
  return {moved:agvs.get('1').x-x0,lag:lastReportX-agvs.get('1').x};
}
const S0=steady(frameOld),S1=steady(frameNew);
console.log(`\n[稳态 6s] 修复前 位移 ${S0.moved.toFixed(2)}m / 滞后 ${S0.lag.toFixed(2)}m`
  +`；修复后 位移 ${S1.moved.toFixed(2)}m / 滞后 ${S1.lag.toFixed(2)}m`);
ok(Math.abs(S1.moved-6)<0.4,`修复后稳态位移应≈真实位移6m（实测 ${S1.moved.toFixed(2)}）`);
ok(Math.abs(S1.lag-S0.lag)<0.05,'稳态滞后不得因本次修复而变化');
ok(S1.lag<0.6,'稳态滞后应在一个上报周期量级内');
ok(agvs.get('1').v<=1.4,'稳态速度不应超真实车速');

// ---- 稳态长跑：滞后必须是有界恒定偏移，不能随时间发散（否则车会越跑越落后）----
agvs.clear();trueX=0;steps=0;clock=Date.now();lastT=clock;feed();
drive(10000,true,frameNew);const lag10=lastReportX-agvs.get('1').x;
drive(50000,true,frameNew);const lag60=lastReportX-agvs.get('1').x;
console.log(`\n[稳态长跑 60s] 滞后: 10s 时 ${lag10.toFixed(2)}m → 60s 时 ${lag60.toFixed(2)}m`);
ok(Math.abs(lag60-lag10)<0.15,`滞后不得随运行时间发散（10s ${lag10.toFixed(2)} → 60s ${lag60.toFixed(2)}）`);

// ---- 冻结标签（JS 全停）恢复：节奏 EMA 不许被挂起时长污染 ----
agvs.clear();trueX=0;steps=0;clock=Date.now();lastT=clock;feed();
drive(5000,true,frameNew);            // 先跑出稳定 EMA(≈208ms)
const gapOk=agvs.get('1')._gap;
clock+=40000;trueX+=40;               // 挂起 40s：期间一条 ingest 都没有（更极端的冻结场景）
feed();                               // 恢复后第一条上报
const gapAfter=agvs.get('1')._gap;
console.log(`\n[节奏 EMA] 挂起前 ${gapOk.toFixed(0)}ms → 挂起40s后首帧 ${gapAfter.toFixed(0)}ms`);
ok(gapAfter<SUSPEND_GAP_MS,`挂起时长不得进 EMA（实测 ${gapAfter.toFixed(0)}ms）`);
ok(agvs.get('1').vCyc<=2,`vCyc 仍须在物理上限内（实测 ${agvs.get('1').vCyc.toFixed(2)}）`);

console.log(bad===0?'\nRESUME PASS：后台切回前台不再补帧冲刺；稳态跟随与节奏 EMA 无回归'
                   :`\nRESUME FAIL ${bad}`);
process.exit(bad?1:0);
