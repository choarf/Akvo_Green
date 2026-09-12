// Formula Engine
export function calculate(data){
 return {
  recovery:(data.permflow/data.feedflow)*100,
  deltaP:data.presion1-data.presion4,
  saltRejection:(1-data.permCond/data.feedCond)*100,
  fouling:((data.presion1-data.presion4)/20)*100
 };
}
