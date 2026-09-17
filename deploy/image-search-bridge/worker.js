const MAX = 5 * 1024 * 1024;
const headers = {"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"};
const page = '<!doctype html><html lang="zh"><meta charset="utf-8"><title>图片临时中转</title><style>body{font:16px system-ui;max-width:700px;margin:48px auto;padding:20px}input,button{font:inherit;margin:8px 0;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:100%}</style><h1>图片临时中转</h1><p>北京时间每日 00:00 自动清理 · JPEG / PNG / WebP / GIF · 最大 5 MB</p><label>本机图片 <input id="file" type="file" accept="image/jpeg,image/png,image/webp,image/gif"></label><pre id="digest"></pre><button id="upload">上传图片</button><hr><label>图片 URL <input id="url" type="url" style="width:90%"></label><button id="import">通过 URL 上传</button><details><summary>上传设置</summary><label>上传密钥 <input id="token" type="password" autocomplete="off"></label></details><pre id="status"></pre><a id="link" target="_blank" rel="noreferrer"></a><script>const el=id=>document.getElementById(id);el("file").onchange=async()=>{const f=el("file").files[0];if(!f)return;const b=await f.arrayBuffer();const h=await crypto.subtle.digest("SHA-256",b);el("digest").textContent="文件 SHA-256: "+Array.from(new Uint8Array(h),v=>v.toString(16).padStart(2,"0")).join("")+"\\n文件大小: "+f.size+" bytes"};async function send(body,type){el("status").textContent="上传中…";el("upload").disabled=el("import").disabled=true;try{const h={};if(type)h["Content-Type"]=type;const t=el("token").value;if(t)h.Authorization="Bearer "+t;const r=await fetch("/upload",{method:"POST",headers:h,body});const data=await r.json();el("status").textContent=JSON.stringify(data,null,2);if(r.ok){el("link").href=data.url;el("link").textContent=data.url}}catch(e){el("status").textContent=String(e)}finally{el("upload").disabled=el("import").disabled=false}}el("upload").onclick=()=>{const f=el("file").files[0];if(!f)return;const d=new FormData();d.set("file",f);send(d)};el("import").onclick=()=>send(JSON.stringify({url:el("url").value}),"application/json");</script></html>';
async function bytes(stream, limit=MAX){if(!stream)throw new Error("Empty image");const reader=stream.getReader();let size=0;const parts=[];try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>limit){await reader.cancel();throw new Error("Image exceeds size limit")}parts.push(value)}}finally{reader.releaseLock()}const result=new Uint8Array(size);let offset=0;for(const part of parts){result.set(part,offset);offset+=part.byteLength}return result;}
async function sha(data){return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",data)),v=>v.toString(16).padStart(2,"0")).join("");}
function mime(b){if(b[0]===255&&b[1]===216&&b[2]===255)return "image/jpeg";if(b[0]===137&&b[1]===80&&b[2]===78&&b[3]===71&&b[4]===13&&b[5]===10&&b[6]===26&&b[7]===10)return "image/png";if(String.fromCharCode(...b.slice(0,4))==="RIFF"&&String.fromCharCode(...b.slice(8,12))==="WEBP")return "image/webp";if(["GIF87a","GIF89a"].includes(String.fromCharCode(...b.slice(0,6))))return "image/gif";throw new Error("Only JPEG, PNG, WebP and GIF images are accepted");}
function json(data,status=200){return Response.json(data,{status,headers});}
export default {
 async fetch(request,env){try{
  const u=new URL(request.url);
  if(request.method==="GET"&&u.pathname==="/")return new Response(page,{headers:{...headers,"Content-Type":"text/html; charset=utf-8"}});
  if((request.method==="GET"||request.method==="HEAD")&&/^\/images\/[a-f0-9]{64}$/.test(u.pathname)){
   const key=u.pathname.slice(8);const item=await env.IMAGES.getWithMetadata(key,{type:"arrayBuffer"});
   if(!item.value||!item.metadata||item.metadata.expiresAt<=Math.floor(Date.now()/1000))return new Response("Not found or expired",{status:404,headers});
   return new Response(request.method==="HEAD"?null:item.value,{headers:{...headers,"Content-Type":item.metadata.contentType,"Content-Length":String(item.value.byteLength)}});
  }
  if(request.method!=="POST"||u.pathname!=="/upload")return json({error:"Not found"},404);
  const authorized=typeof env.UPLOAD_TOKEN==="string"&&env.UPLOAD_TOKEN.length>0&&request.headers.get("Authorization")==="Bearer "+env.UPLOAD_TOKEN;
  if(!authorized)return json({error:"Invalid upload API key"},401);
  const origin=request.headers.get("Origin");if(origin&&origin!==u.origin)return json({error:"Origin not allowed"},403);
  const type=request.headers.get("Content-Type")||"";let data;let source="file";
  if(type.startsWith("application/json")){
   const raw=await bytes(request.body,16384);const input=JSON.parse(new TextDecoder().decode(raw));const remote=new URL(input.url);
   if(remote.protocol!=="https:"||remote.username||remote.password||remote.port||!remote.hostname.includes(".")||/^[\d.]+$/.test(remote.hostname)||remote.hostname.includes(":")||/(^|\.)(localhost|local|internal|test|invalid)$/.test(remote.hostname))return json({error:"URL import requires a public HTTPS image URL"},400);
   if(remote.origin===u.origin){if(!/^\/images\/[a-f0-9]{64}$/.test(remote.pathname))return json({error:"Invalid bridge image URL"},400);const item=await env.IMAGES.getWithMetadata(remote.pathname.slice(8),{type:"arrayBuffer"});if(!item.value||!item.metadata||item.metadata.expiresAt<=Math.floor(Date.now()/1000))return json({error:"Source image expired"},404);data=new Uint8Array(item.value);}else{const response=await fetch(remote,{redirect:"error",signal:AbortSignal.timeout(20000)});if(!response.ok)return json({error:"Source returned HTTP "+response.status},502);data=await bytes(response.body);}source="url";
  }else if(type.startsWith("image/")){data=await bytes(request.body);
  }else if(type.startsWith("multipart/form-data")){
   const raw=await bytes(request.body,MAX+65536);const form=await new Response(raw,{headers:{"Content-Type":type}}).formData();const file=form.get("file");if(!(file instanceof File))return json({error:"Missing file"},400);if(file.size>MAX)return json({error:"Image exceeds size limit"},413);data=new Uint8Array(await file.arrayBuffer());
  }else return json({error:"Use image bytes, multipart file upload or JSON URL"},415);
  const contentType=mime(data);const hash=await sha(data);
  const now=Math.floor(Date.now()/1000);const expiresAt=(Math.floor((now+28800)/86400)+1)*86400-28800;
  if(expiresAt-now<60)return json({error:"Daily cleanup is imminent; upload after midnight"},503);
  const existing=await env.IMAGES.getWithMetadata(hash,{type:"arrayBuffer"});const cached=!!existing.value&&existing.metadata?.expiresAt>now;
  if(!cached)await env.IMAGES.put(hash,data,{expiration:expiresAt,metadata:{contentType,expiresAt,createdAt:now}});
  return json({url:u.origin+"/images/"+hash,sha256:hash,bytes:data.byteLength,contentType,source,reused:cached,expiresAt:new Date((cached?existing.metadata.expiresAt:expiresAt)*1000).toISOString(),cleanupTimezone:"Asia/Shanghai",cleanupTime:"00:00"});
 }catch(error){return json({error:String(error.message||error)},400)}}
};
