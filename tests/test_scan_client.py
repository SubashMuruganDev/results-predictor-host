"""Execute the shipped client with controlled fetch responses, without a browser dependency."""
import shutil
import pytest
import subprocess
from results_predictor.web import HTML


@pytest.mark.skipif(shutil.which('node') is None, reason='Client execution test requires Node.js')
def test_scan_client_errors_and_polling():
    script = HTML.split('<script>')[1].split('</script>')[0]
    harness = r'''
const assert = require('node:assert/strict');
const elements = new Map();
global.document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {value:'', checked:true, textContent:'', classList:{add(){},remove(){},toggle(){}}, innerHTML:''});
 return elements.get(id);
}};
global.sessionStorage = {getItem(){return '';},setItem(){}};
global.location = {pathname:'/'};
let calls=[];
global.fetch=async (...args)=>{calls.push(args);throw Error('unexpected network');};
'''
    checks = r'''
(async()=>{
 await $('form').onsubmit({preventDefault(){}});
 assert.equal(calls.length,0);
 assert.equal($('scanError').textContent,'Enter the write token to scan');
 await $('score').onclick();
 assert.equal(calls.length,0);
 assert.match($('scoreError').textContent,/Enter the write token/);
 $('writeToken').value='test-token';
 global.fetch=async()=>({status:502,ok:false,text:async()=>'<html>Bad gateway</html>'});
 await $('form').onsubmit({preventDefault(){}});
 assert.match($('scanError').textContent,/502/);
 assert.doesNotMatch($('scanError').textContent,/SyntaxError|Unexpected token/);
 global.fetch=async()=>({status:401,ok:false,text:async()=>JSON.stringify({error:'Write token required'})});
 await $('form').onsubmit({preventDefault(){}});
 assert.match($('scanError').textContent,/write token/i);
 const nativeTimeout=global.setTimeout;
 global.setTimeout=(fn,ms)=>nativeTimeout(fn,ms===180000?0:ms);
 global.fetch=async(path,opts)=>new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Object.assign(Error(),{name:'AbortError'}))));
 await $('form').onsubmit({preventDefault(){}});
 assert.match($('scanError').textContent,/timed out/i);
 global.setTimeout=(fn)=>nativeTimeout(fn,0);
 calls=[];
 global.fetch=async(path,opts)=>{
  calls.push([path,opts]);
  assert.equal($('scanBtn').disabled,true);
  const data=calls.length===1?{job_id:'abc',status:'running'}:calls.length===2?{status:'running'}:{status:'done',rows:[],events:[],offline:true};
  return {status:200,ok:true,text:async()=>JSON.stringify(data)};
 };
 await $('form').onsubmit({preventDefault(){}});
 assert.equal(JSON.parse(calls[0][1].body).async,true);
 assert.equal(calls[1][0],'/api/jobs/abc');
 assert.equal(calls[1][1].headers['X-Write-Token'],'test-token');
 assert.equal(calls.length,3);
 assert.equal($('scanBtn').disabled,false);
 assert.equal($('scanError').textContent,'');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', harness + script + checks], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
