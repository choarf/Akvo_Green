// Modbus REST placeholder
export async function getLiveData(){
 return await fetch('/api/live').then(r=>r.json());
}
