const alarms=[['Critical','High Pressure P4','No'],['Warning','Low Flow','No'],['Info','CIP Due','Yes']];
async function run(){
let cfg=await fetch('config/dashboard.json').then(r=>r.json());
let g=document.getElementById('gauges');
cfg.gauges.forEach(x=>{
let d=document.createElement('div');d.className='card gauge';d.id=x.id;g.appendChild(d);
Highcharts.chart(x.id,{chart:{type:'solidgauge'},title:{text:x.title},
pane:{startAngle:-90,endAngle:90,center:['50%','85%'],background:{shape:'arc',innerRadius:'60%',outerRadius:'100%'}},
yAxis:{min:0,max:x.max,stops:[[0,'#1565C0'],[0.6,'#42A5F5'],[0.8,'#FB8C00'],[1,'#D50000']],plotLines:[{value:x.target,width:4,color:'#FFD54F'}],labels:{enabled:false},tickWidth:0,lineWidth:0},
credits:{enabled:false},series:[{data:[x.value],dataLabels:{format:'<div style="text-align:center"><span style="font-size:26px">{y}</span><br>'+x.unit+'</div>'}}]});
});
Highcharts.chart('trend',{chart:{type:'areaspline'},title:{text:'24 Hour Flow'},credits:{enabled:false},legend:{enabled:false},series:[{data:[80,82,83,84,82,86,89,88,91,90]}]});
Highcharts.chart('cleaning',{chart:{type:'column'},title:{text:'CIP Effectiveness'},credits:{enabled:false},xAxis:{categories:['1','2','3','4','5']},series:[{name:'Before',data:[2.5,2.2,2.3,2.1,2.4]},{name:'After',data:[1.2,1.1,1.3,1.0,1.2]}]});
let tb=document.getElementById('alarmBody');alarms.forEach(a=>{let r=tb.insertRow();a.forEach(v=>{let c=r.insertCell();c.textContent=v;});});
setInterval(()=>{
document.getElementById('health').textContent='CPU '+(15+Math.floor(Math.random()*15))+'% | RAM '+(35+Math.floor(Math.random()*20))+'% | Temp '+(45+Math.floor(Math.random()*6))+'°C';
Highcharts.charts.forEach(c=>{if(c&&c.series[0]&&c.series[0].points.length){let p=c.series[0].points[0];if(typeof p.y==='number'){p.update(Math.max(0,Math.min(c.yAxis[0].max,p.y+(Math.random()-0.5)*4)),true,false);}}});
},1000);
document.getElementById('theme').onclick=()=>document.body.classList.toggle('dark');
}
run();
