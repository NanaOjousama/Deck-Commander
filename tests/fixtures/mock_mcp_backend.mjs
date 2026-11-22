// Fake backend for disposable gateway integration ONLY. Never executes commands.
import { appendFileSync } from 'node:fs';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
const label = process.env.DC_TEST_DEVICE || 'steamdeck';
const log = value => { if (process.env.DC_TEST_BACKEND_LOG) appendFileSync(process.env.DC_TEST_BACKEND_LOG, `${label}:${process.pid}:${value}\n`); };
const server = new Server({name:'disposable-'+label,version:'0.0.0'}, {capabilities:{tools:{}}});
server.setRequestHandler(ListToolsRequestSchema, async () => ({tools:[{
  name:'start_process',description:'Fake command; never executes.',
  inputSchema:{type:'object',properties:{command:{type:'string'},timeout_ms:{type:'number'}}}
}]}));
server.setRequestHandler(CallToolRequestSchema, async req => {
  log('call:'+req.params.name);
  return {content:[{type:'text',text:'MOCK_'+label+'_'+req.params.name}]};
});
log('connected');
await server.connect(new StdioServerTransport());
