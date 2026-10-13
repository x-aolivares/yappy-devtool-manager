const fs=require('fs');
const html=fs.readFileSync('mockups/workflow.html','utf8');
console.log('1. leido', html.length);
const store=new Map();
global.localStorage={getItem:k=>store.has(k)?store.get(k):null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k),clear:()=>store.clear()};
console.log('2. localStorage listo');
const rendered={};const inputs={env:'dev',cluster:'cap'};
global.document={
  getElementById:(id)=>({get value(){return inputs[id]??''},set innerHTML(v){rendered[id]=v},get innerHTML(){return rendered[id]||''},set textContent(v){rendered[id]=v},get textContent(){return rendered[id]||''},set hidden(v){},scrollTop:0,scrollIntoView(){},addEventListener(){},querySelectorAll:()=>[]}),
  querySelector:(sel)=>({value:sel.includes('cluster')?inputs.cluster:inputs.env}),
  querySelectorAll:()=>[{addEventListener(){}}],
  addEventListener(){},
};
global.setTimeout=()=>0;
console.log('3. dom listo');
const js=html.match(/<script>([\s\S]*?)<\/script>/)[1]+'\nreturn { catalog, runtime, portList, saveCatalog, render, deletePort, DEFAULT_CATALOG, clusterSel };';
console.log('4. js extraido', js.length);
const t=new Function(js)();
console.log('5. evaluado');
t.render();
console.log('6. render ok');
process.exit(0);
