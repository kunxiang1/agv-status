// 告警闸门回归：从 index.html 实时抽 ownerOf/handle/handleAlarm，喂样本断言去留
// 用法: node _alarm_test.js [index.html路径，默认 <项目根>/index.html]
const fs=require('fs'),path=require('path');
const SRC=process.argv[2]||path.join(__dirname,'..','index.html');
const js=fs.readFileSync(SRC,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const ex=re=>{const m=js.match(re);if(!m)throw new Error('extract fail: '+re);return m[0];};
global.window={}; eval(fs.readFileSync(SRC.replace(/index\.html$/,'alarm.js'),'utf8').replace('ALARM_DB','window.ALARM_DB'));
const alarms=[]; global.agvs=new Map(); global.curMap='CC'; global.map=null;
global.$=()=>({style:{},innerHTML:'',textContent:''});
global.renderAlarms=()=>{}; global.ingest=()=>{};
eval(ex(/function ownerOf[\s\S]*?\n}/));
eval(ex(/function handle\(m\)\{[\s\S]*?\n\}\n(?=function handleAlarm)/).replace('function handle','var handle=function'));
eval(ex(/function handleAlarm[\s\S]*?\n}/));
let bad=0; const ok=(c,m)=>{if(!c){bad++;console.log('FAIL',m);}};
const t=s=>new Date(Date.now()+s).toISOString().slice(0,19).replace("T"," ");
// 1) SN 归属→丢  2) 字典无 41→丢  3) >24h 僵尸→丢  4) 新鲜 #8→收
handle({e:'alarm',mt:'5',st:'1',stat:'1',p1:'1A04C4E14BA0J9Y_ECS',src:'RCS#3(engine-ip)',time:t(-1000),map:'CC',guid:'a'});
handle({e:'alarm',mt:'41',st:'3',stat:'1',p1:'1F9E775D-D31',src:'x',time:t(-1000),map:'CC',guid:'b'});
handle({e:'alarm',mt:'5',st:'4',stat:'1',p1:'8',src:'x',time:t(-86400*1000*7),map:'CC',guid:'c'});
handle({e:'alarm',mt:'5',st:'4',stat:'1',p1:'8',src:'x',time:t(-60000),map:'CC',guid:'d',lvl:'3'});
ok(alarms.length===1&&alarms[0].guid==='d','应只剩 #8 新鲜可译告警，实际 '+alarms.map(a=>a.guid));
ok(alarms[0]&&alarms[0]._own==='8'&&alarms[0].txt==='地码补光不足','#8 归属/译文断言');
// 5) 跨图→丢  6) 恢复→移除对应 guid（即便从未入列也安全）
handle({e:'alarm',mt:'5',st:'4',stat:'1',p1:'8',src:'x',time:t(-1000),map:'BB',guid:'e'});
handle({e:'alarm',mt:'5',st:'4',stat:'0',p1:'8',src:'x',time:t(-1000),map:'CC',guid:'d'});
ok(alarms.length===0,'跨图不应入列 & 恢复应移除 d');
// 7) 非告警事件不受影响：offline 置失联
const car={robotCode:'9',online:true,_seen:Date.now()}; agvs.set('9',car);
handle({e:'offline',map:'CC',ids:['9']});
ok(car.online===false,'offline 应置失联');
console.log(bad?`ALARM GATE FAIL ${bad}`:'ALARM GATE PASS 7 用例');
process.exit(bad?1:0);
