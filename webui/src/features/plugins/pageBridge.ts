/** A sandbox page can call only its owning plugin through the authenticated host. */
export interface PageRequest {
  type: "nahida.plugin.page.request";
  session: string;
  id: string;
  action: string;
  payload: Record<string, unknown>;
}

export function parsePageRequest(
  value: unknown,
  session: string,
): PageRequest | null {
  if (!value || typeof value !== "object" || !session) return null;
  const item = value as Record<string, unknown>;
  if (
    item.type !== "nahida.plugin.page.request" ||
    item.session !== session ||
    typeof item.id !== "string" ||
    !/^[\w-]{1,64}$/.test(item.id) ||
    typeof item.action !== "string" ||
    !/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(item.action) ||
    !item.payload ||
    typeof item.payload !== "object" ||
    Array.isArray(item.payload)
  )
    return null;
  try {
    if (JSON.stringify(item.payload).length > 65536) return null;
  } catch {
    return null;
  }
  return item as unknown as PageRequest;
}

export function pageBridgeBootstrap(session: string): string {
  const encoded = JSON.stringify(session).replaceAll("<", "\\u003c");
  return `(()=>{
    const session=${encoded};
    const pending=new Map();
    let sequence=0;
    window.addEventListener("message",event=>{
      const message=event.data;
      if(event.source!==window.parent || !message ||
         message.type!=="nahida.plugin.page.response" || message.session!==session) return;
      const request=pending.get(message.id);
      if(!request) return;
      pending.delete(message.id); clearTimeout(request.timer);
      if(message.error) request.reject(new Error(message.error));
      else request.resolve(message.result);
    });
    window.nahidaPlugin=Object.freeze({request(action,payload={}){
      if(pending.size>=8) return Promise.reject(new Error("Too many pending page requests"));
      const id=String(++sequence);
      return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{pending.delete(id);reject(new Error("Plugin request timed out"));},15000);
        pending.set(id,{resolve,reject,timer});
        window.parent.postMessage({type:"nahida.plugin.page.request",session,id,action,payload},"*");
      });
    }});
  })();`;
}
