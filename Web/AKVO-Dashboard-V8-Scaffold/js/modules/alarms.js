export function evaluate(d){
 const alarms=[];
 if(d.deltaP>20) alarms.push({priority:'Critical',message:'High Delta P'});
 if(d.recovery<70) alarms.push({priority:'Warning',message:'Low Recovery'});
 return alarms;
}
