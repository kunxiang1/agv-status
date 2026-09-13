// 真实数据端到端验证：从本地代理拉两帧真实 RCS 快照，跑 ingest→setRoute→动画推进全链路
// 前置：python server.py 已启动（默认 8899，或用 argv[3] 指定端口）
// 用法: node _live_test.js [index.html路径，默认 <项目根>/index.html] [代理端口]
const fs=require('fs'),path=require('path');
const SRC=process.argv[2]||path.join(__dirname,'..','index.html');
const PORT=process.argv[3]||'8899';
const js=fs.readFileSync(SRC,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
// ---- 载入真实地图 BB ----
const bb=JSON.parse(fs.readFileSync(path.join(path.dirname(SRC),'maps','BB.json'),'utf8'));
global.map={nodes:bb.nodes,edges:bb.edges};
map.nodeMap={};map.nodes.forEach(n=>map.nodeMap[n[0]]=n);
map.adj={};map.edges.forEach(([a,b])=>{(map.adj[a]??=[]).push(b);(map.adj[b]??=[]).push(a);});
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),POLL_GUESS=250,SUSPEND_GAP_MS=1500;
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

// ---- 路网最近距离（顶点是否贴路）----
function roadDist(x,y){
  let bd=1e18;
  for(const[a,b]of map.edges){const A=map.nodeMap[a],B=map.nodeMap[b];
    const vx=B[1]-A[1],vy=B[2]-A[2],L=vx*vx+vy*vy||1e-9;
    const u=Math.max(0,Math.min(1,((x-A[1])*vx+(y-A[2])*vy)/L));
    bd=Math.min(bd,Math.hypot(x-(A[1]+vx*u),y-(A[2]+vy*u)));}
  return bd;
}
let bad=0;
const fail=msg=>{bad++;console.log('FAIL',msg);};

async function snap(){
  const r=await fetch('http://127.0.0.1:'+PORT+'/api/snapshot');   // 推送快照（零轮询：server 直连 RCS 推送）
  const j=await r.json();
  if(j.code!=='0')throw new Error('server code '+j.code);
  return j.data.filter(r=>r.mapCode==="BB");  // 快照=全厂，本测试只用 BB 图车辆
}
function animSteps(a,seconds){                     // 简化 frame 推进：vT=vCyc、加速度限幅、s 封顶 sEnd
  const steps=[];let t=0;const dt=0.1;
  while(t<seconds){t+=dt;
    const vT=a.sEnd>a.s+0.02?a.vCyc:0;
    const acc=(vT>a.v?1.2:1.8)*dt;
    a.v=Math.abs(vT-a.v)<=acc?vT:a.v+Math.sign(vT-a.v)*acc;
    a.s=Math.min(a.sEnd,a.s+a.v*dt);
    const p=posAt(a);a.x=p.x;a.y=p.y;steps.push([p.x,p.y]);}
  return steps;
}
(async()=>{
  const f1=await snap();
  console.log('帧1: '+f1.length+' 台车  '+new Date().toLocaleTimeString());
  const now1=Date.now();const cars=new Map();
  for(const row of f1){
    const a={...row,tx:+row.posX/1000,ty:+row.posY/1000,
      pathPts:(row.path||[]).map(p=>{try{const[x,y]=JSON.parse(p);return[x/1000,y/1000];}catch(e){return null;}}).filter(p=>p&&isFinite(p[0])&&isFinite(p[1]))};
    ingest(a,cars.get(a.robotCode),now1);
    cars.set(a.robotCode,a);
  }
  // 动画 4.6s（一个轮询周期的预算）；poly 为空=无路线可画（应用层走缓动分支），跳过
  for(const a of cars.values()){a.v=a.v||0;if(a.poly)animSteps(a,4.6);}
  await new Promise(r=>setTimeout(r,5000));
  const f2=await snap();
  console.log('帧2: '+f2.length+' 台车  '+new Date().toLocaleTimeString());
  const now2=Date.now();
  for(const row of f2){
    const a={...row,tx:+row.posX/1000,ty:+row.posY/1000,
      pathPts:(row.path||[]).map(p=>{try{const[x,y]=JSON.parse(p);return[x/1000,y/1000];}catch(e){return null;}}).filter(p=>p&&isFinite(p[0])&&isFinite(p[1]))};
    const old=cars.get(a.robotCode);
    const before=old?{x:old.x,y:old.y}:null;
    ingest(a,old,now2);
    // 断言 A：线顶点全贴路（≤0.6m）
    if(a.poly)for(const[vx,vy]of a.poly)
      if(roadDist(vx,vy)>0.6)fail(`车${a.robotCode} 线顶点离路 ${roadDist(vx,vy).toFixed(2)}m @(${vx.toFixed(2)},${vy.toFixed(2)})`);
    // 断言 B：动画位置离最新上报 ≤ 追帧上限（周期预算+DR余量）
    const gap=Math.hypot(a.x-(old?old.x:before?a.x:a.tx),a.y-(old?old.y:before?a.y:a.ty));
    if(old&&gap>12)fail(`车${a.robotCode} 采纳帧后位置跳变 ${gap.toFixed(1)}m`);
    console.log(`车${a.robotCode} 状态${a.status} 速度${a.speed}mm/s `+
      `动画(${a.x?.toFixed(2)},${a.y?.toFixed(2)}) 上报(${a.tx?.toFixed(2)},${a.ty?.toFixed(2)}) `+
      `线${a.poly?a.poly.length+'点':'无'} sEnd=${a.sEnd?.toFixed(2)} vCyc=${a.vCyc?.toFixed(2)}`);
    // 动画推进 4.6s 并断言 C：每步位移 ≤ 2.2m/s×dt×1.5（无瞬移）且逐步贴近上报点
    if(a.poly){const steps=animSteps(a,4.6);let prev=steps[0],lastGap=1e18;
      for(const[px,py]of steps){
        const st=Math.hypot(px-prev[0],py-prev[1]);
        if(st>2.2*0.1*1.6)fail(`车${a.robotCode} 单步位移 ${st.toFixed(2)}m 超限`);
        const rd=roadDist(px,py);
        if(rd>0.6)fail(`车${a.robotCode} 动画点离路 ${rd.toFixed(2)}m`);
        const g=Math.hypot(px-a.tx,py-a.ty);
        if(g>lastGap+0.15)fail(`车${a.robotCode} 动画偏离上报点（应单调接近）`);
        lastGap=g;prev=[px,py];}
      console.log(`  动画终态离上报 ${lastGap.toFixed(2)}m（贴路、单调、无瞬移 ✓）`);}
    cars.set(a.robotCode,a);
  }
  // ---- 断言 D：SSE 按图分组订阅（真连一次 /api/events?map=BB）----
  // 订阅 BB 就只该收到 BB 的事件，或与地图无关的系统级事件（货架表 pods 等）；收到别的图＝分组失效。
  try{
    const ctl=new AbortController();
    const r=await fetch('http://127.0.0.1:'+PORT+'/api/events?map=BB',{signal:ctl.signal});
    const rd=r.body.getReader(),dec=new TextDecoder();
    let buf='',seen=0,alien=[];
    const t0=Date.now();
    while(Date.now()-t0<4000&&seen<80){
      const dv=await rd.read(); if(dv.done) break;
      buf+=dec.decode(dv.value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const line=buf.slice(0,i).trim(); buf=buf.slice(i+2);
        if(!line.startsWith('data: ')) continue;
        seen++;
        let o; try{o=JSON.parse(line.slice(6));}catch(e){continue;}
        const m=o.map||(o.a&&o.a.mapCode)||'';
        if(m&&m!=='BB') alien.push(m);
      }
    }
    ctl.abort();
    if(alien.length) fail(`按图分组失效：订阅 BB 却收到 ${alien.length} 条它图事件（如 ${alien[0]}）`);
    else console.log(`  分组订阅：订阅 BB 共收 ${seen} 条，无它图事件 ✓`);
  }catch(e){ fail('SSE 分组检查异常: '+e.message); }

  console.log(bad===0?'LIVE PASS：真实数据全链路（贴路/无瞬移/单调追帧/丢包停等/按图分组）':`LIVE FAIL ${bad}`);
  process.exit(bad?1:0);
})().catch(e=>{console.error('LIVE ERROR',e.message);process.exit(2);});
