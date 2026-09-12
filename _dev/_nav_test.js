// 导航式运动核心断言：从 index.html 实时抽函数，防止测试与实现脱钩
// 用法: node _nav_test.js [index.html 路径，默认 <项目根>/index.html]
const fs=require('fs'),path=require('path');
const SRC=process.argv[2]||path.join(__dirname,'..','index.html');
const js=fs.readFileSync(SRC,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
global.map={nodes:[[1,7,10,16],[2,8,10,16],[3,8,12,16],[4,9,12,16],[5,9,14,16]],
  edges:[[1,2],[2,3],[3,4],[4,5]]};
map.nodeMap={};map.nodes.forEach(n=>map.nodeMap[n[0]]=n);
map.adj={};map.edges.forEach(([a,b])=>{(map.adj[a]??=[]).push(b);(map.adj[b]??=[]).push(a);});
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),POLL_GUESS=250;
const ex=re=>{const m=js.match(re);if(!m)throw new Error('extract fail: '+re);return m[0];};
eval(ex(/function snapNode[\s\S]*?\n}/));
eval(ex(/const dot3=[^\n]*/).replace('const dot3','var dot3'));   // const 在 eval 内不外泄，转 var
eval(ex(/function graphRoute[\s\S]*?\n}/));
eval(ex(/function buildPoly[\s\S]*?\n}/));
eval(ex(/function posAt[\s\S]*?\n}/));
eval(ex(/function projLen[\s\S]*?\n}/));
eval(ex(/function resolveHead[\s\S]*?\n}/));
eval(ex(/function setRoute[\s\S]*?\n}/));
let bad=0;
const pure=(name,pts)=>{for(let i=0;i<pts.length-1;i++){       // 全程不许斜穿
  const dx=Math.abs(pts[i][0]-pts[i+1][0]),dy=Math.abs(pts[i][1]-pts[i+1][1]);
  if(dx>0.12&&dy>0.12){bad++;console.log('DIAGONAL',name,pts[i],pts[i+1]);}}};
const mono=(name,pts)=>{for(let i=0;i<pts.length-2;i++){       // 相邻段不许反折（先走A又原路退回B）
  const d1=[pts[i+1][0]-pts[i][0],pts[i+1][1]-pts[i][1]],
        d2=[pts[i+2][0]-pts[i+1][0],pts[i+2][1]-pts[i+1][1]];
  if(d1[0]*d2[0]+d1[1]*d2[1]<-0.01){bad++;console.log('FOLD',name,i,JSON.stringify(pts));}}};
const na=x=>{while(x>Math.PI)x-=2*Math.PI;while(x<-Math.PI)x+=2*Math.PI;return x;};

// 1) 反折消除：车(7.3,10)与目标(7.8,10)在同一条边上——不许先退到节点7再前进到8再退回
let r=graphRoute(7.3,10,7.8,10);
pure("fold-same-edge",r);mono("fold-same-edge",r);
if(r.length!==2){bad++;console.log('同边应直连（砍吸附点）',JSON.stringify(r));}
// 1b) 跨拐角贴路：直角路径，无斜穿无反折
r=graphRoute(7.3,10,8,12);
pure("corner",r);mono("corner",r);
if(r.length<3){bad++;console.log('拐角应有中间节点',JSON.stringify(r));}
// 1c) 离路目标收到最近节点（地图版本落后）：目标(9,17)→只到(9,14)
r=graphRoute(9,12,9,17);
if(Math.hypot(r.at(-1)[0]-9,r.at(-1)[1]-14)>0.01){bad++;console.log('离路目标应收到最近节点',JSON.stringify(r));}
pure("offroad",r);
// 2) 投影起步：车在轨迹起点 → s≈0
let a={x:7,y:10,ang:0,robotDir:"0"};
setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[7,10]);
pure("start",a.poly);
if(a.s>0.05){bad++;console.log("起步 s 应≈0，实际",a.s);}
// 3) 追帧与停等：动画落后(7.3)、上报(7.8) → vCyc=0.5/4.6；追平后停等
a.x=7.8;a.y=10;setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[7.8,10]);
if(Math.hypot(a.poly[0][0]-7.8,a.poly[0][1]-10)>1e-9){bad++;console.log("线头没锚在车位置");}
if(a.s>0.01||a.vCyc>0.01){bad++;console.log("追平后应停等",a.s,a.vCyc);}
a.x=7.3;a.y=10;setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[7.8,10],4.6);   // 显式 cyc=4.6s(模拟丢帧长周期)
if(Math.abs(a.sEnd-0.5)>0.05){bad++;console.log("sEnd 应=0.5",a.sEnd);}
if(Math.abs(a.vCyc-0.5/4.6)>0.02){bad++;console.log("vCyc 应=0.5/4.6",a.vCyc);}
a.x=7.3;a.y=10;setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[7.8,10]);       // 无 cyc→POLL_GUESS 0.2s 节奏
if(Math.abs(a.vCyc-Math.min(0.5/(250/1000*0.8),2))>0.05){bad++;console.log("默认节奏 vCyc 应=2(clamp)",a.vCyc);}
// 4) 滞后帧停等：车已走到 7.9（s=0.6），上报 7.5（在车头之后）→ 原地停等，绝不回走
a.s=0.6;
setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[7.5,10]);
if(a.sEnd!==a.s){bad++;console.log("滞后帧应停等 sEnd==s",a.sEnd,a.s);}
if(a.vCyc!==0){bad++;console.log("滞后帧 vCyc 应=0",a.vCyc);}
// 5) 转弯点限速（90° cap=0.25）
a={x:7,y:10,ang:0,robotDir:"0"};
setRoute(a,[[7,10],[8,10],[8,12],[9,12],[9,14]],[7,10]);
const c=a.corners.find(c=>Math.abs(c.s-1)<0.01);
if(!c||c.cap!==0.25){bad++;console.log("corner cap 错",JSON.stringify(a.corners));}
// 6) posAt 连续性：全弧长扫过无跳变
let prev=null,maxjump=0;
for(let s=0;s<=a.cum.at(-1);s+=0.01){a.s=s;const p=posAt(a);
  if(prev)maxjump=Math.max(maxjump,Math.hypot(p.x-prev[0],p.y-prev[1]));prev=[p.x,p.y];}
if(maxjump>0.05){bad++;console.log("posAt 跳变",maxjump);}
// 7) DR 尾部延展：path 冻结、车已追到线尾，上报点越过线尾 → 沿路网延展到上报点，车不悬空
a={x:8.9,y:12,ang:-Math.PI/2,robotDir:"90"};
setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[9,12]);
a.s=a.cum[a.cum.length-1];
setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[9,14]);
pure("dr-ext",a.poly);mono("dr-ext",a.poly);
if(Math.hypot(a.poly.at(-1)[0]-9,a.poly.at(-1)[1]-14)>0.01){bad++;console.log("DR 应延展到(9,14)",a.poly.at(-1));}
if(a.sEnd<=a.s||a.vCyc<=0){bad++;console.log("DR 应继续推进",a.sEnd,a.vCyc);}
// 8) 上报点离路（>0.55m，地图版本差）：不追离路点，延到最近节点为止
a={x:8.9,y:12,ang:-Math.PI/2,robotDir:"90"};
setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[9,12]);
a.s=a.cum[a.cum.length-1];
setRoute(a,[[7,10],[8,10],[8,12],[9,12]],[9,17]);
if(Math.hypot(a.poly.at(-1)[0]-9,a.poly.at(-1)[1]-14)>0.01){bad++;console.log("离路延展应停在(9,14)",a.poly.at(-1));}
// 9) 车头仲裁：真实朝向优先；反向=倒行；侧向/缺失=随线
let h=resolveHead(0,"0");   if(h[0]!==0||h[1]){bad++;console.log("正行应朝切向且非倒行");}
h=resolveHead(0,"180");    if(Math.abs(na(h[0]-Math.PI))>0.01||!h[1]){bad++;console.log("反向应倒行");}
h=resolveHead(0,"90");     if(h[0]!==0||h[1]){bad++;console.log("侧向90°应随线");}
h=resolveHead(0,"");       if(h[0]!==0||h[1]){bad++;console.log("空方向应随线");}
h=resolveHead(0,null);     if(h[0]!==0||h[1]){bad++;console.log("null方向应随线");}
// 10) 无 path 清线
a={x:7,y:10,ang:0,poly:[[7,10]]};setRoute(a,[],[7,10]);
if(a.poly){bad++;console.log("无任务应清线");}
// 11) 多周期仿真：车 1m/s 沿路走，path 逐周期延展 → 动画位置沿路弧长单调不减
const nodesPath=[[7,10],[8,10],[8,12],[9,12],[9,14]];
const pathAt=t=>{let s=0;for(let i=1;i<nodesPath.length;i++){
  const L=Math.hypot(nodesPath[i][0]-nodesPath[i-1][0],nodesPath[i][1]-nodesPath[i-1][1]);
  if(s+L>=t){const u=(t-s)/L;return[nodesPath[i-1][0]+(nodesPath[i][0]-nodesPath[i-1][0])*u,nodesPath[i-1][1]+(nodesPath[i][1]-nodesPath[i-1][1])*u];}s+=L;}
  return nodesPath.at(-1);};
const arcOf=(x,y)=>{let best=0,bd=1e18,s=0;for(let i=1;i<nodesPath.length;i++){
  const ax=nodesPath[i-1],bx=nodesPath[i],L=Math.hypot(bx[0]-ax[0],bx[1]-ax[1])||1e-9;
  const u=Math.max(0,Math.min(1,((x-ax[0])*(bx[0]-ax[0])+(y-ax[1])*(bx[1]-ax[1]))/(L*L)));
  const px=ax[0]+(bx[0]-ax[0])*u,py=ax[1]+(bx[1]-ax[1])*u,dd=Math.hypot(x-px,y-py);
  if(dd<bd){bd=dd;best=s+u*L;}s+=L;}return best;};
let car={x:7,y:10,ang:0,robotDir:"0"};let lastArc=0;
const nodeArc=[0,1,3,4,6];                                // 节点弧长（7→8:1m, 8→8,12:2m, 8,12→9,12:1m, 9,12→9,14:2m）
for(let k=1;k<=6;k++){
  const P=pathAt(k);                                    // 真车位置（1m/s）
  let np=[nodesPath[0]];                                // 已走历史 = 严格身后节点 + 当前位置
  for(let i=1;i<nodesPath.length;i++)if(nodeArc[i]<k-0.01)np.push(nodesPath[i]);
  if(Math.hypot(P[0]-np.at(-1)[0],P[1]-np.at(-1)[1])>0.01)np.push(P);
  setRoute(car,np,P,4.6);
  if(!car.poly)continue;                                // 车停在轨迹尾且无延展需求：应用层走缓动分支，位置不变
  car.s=car.sEnd;const p=posAt(car);car.x=p.x;car.y=p.y;   // 动画走完本周期预算
  const arc=arcOf(car.x,car.y);
  if(arc<lastArc-0.05){bad++;console.log("周期",k,"位置沿路回退",lastArc.toFixed(2),"→",arc.toFixed(2));}
  lastArc=arc;
}
// 12) path 冻结（RSSI 故障不回轨迹）但坐标正常上报 → DR 延展逐周期追，位置仍单调
let car2={x:7,y:10,ang:0,robotDir:"0"};let lastArc2=0;
const frozenNp=[[7,10],[8,10],[8,12],[9,12]];
for(let k=1;k<=6;k++){
  const P=pathAt(k);                                    // 车实际继续走（1m/s），轨迹尾冻在(9,12)
  setRoute(car2,frozenNp,P,4.6);
  if(!car2.poly)continue;
  car2.s=car2.sEnd;const p=posAt(car2);car2.x=p.x;car2.y=p.y;
  const arc=arcOf(car2.x,car2.y);
  if(arc<lastArc2-0.05){bad++;console.log("DR周期",k,"回退",lastArc2.toFixed(2),"→",arc.toFixed(2));}
  lastArc2=arc;
}
if(arcOf(car2.x,car2.y)<3.9){bad++;console.log("DR 应追到近线尾，实际弧长",arcOf(car2.x,car2.y).toFixed(2));}
console.log(bad===0?"PASS 12 组用例（反折消除/贴路/离路钳制/起步/追帧/滞后停等/限速/连续性/DR延展/车头仲裁/清线/多周期单调）":`FAIL ${bad}`);
process.exit(bad?1:0);
