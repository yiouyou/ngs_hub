// Run: node --test tests/desk_order_details_events.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'ngs_hub');

function harness() {
  const handlers = {};
  const row = {name:'ORDER-ROW',project_type:'WGS',service:'WGS_30X',sample_type:'DNA',species:'human',
    quantity:1,read_depth:'45x',data_analysis:'No',unit_price:576,amount:576,onsite_service:0};
  let html = '';
  const frm = {doc:{doctype:'NGS Order',items:[row]},refresh_field(){},
    fields_dict:{item_details:{$wrapper:{html(value){html=value;}}}}};
  const frappe = {ui:{form:{on(doctype,events){handlers[doctype]=events;}}},
    format(value){return '$'+value.toFixed(2);},
    model:{set_value:async(cdt,cdn,field,value)=>{
      await Promise.resolve();
      assert.equal(cdt,'NGS Order Item');assert.equal(cdn,row.name);
      row[field]=value;
    }}};
  const context = vm.createContext({frappe,locals:{'NGS Order Item':{[row.name]:row}},flt:Number,__:(v)=>v});
  vm.runInContext(fs.readFileSync(path.join(root,'public/js/desk_item_details.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(root,'ngs_hub/doctype/ngs_order/ngs_order.js'),'utf8'),context);
  return {frappe,handlers,row,frm,get html(){return html;}};
}

test('Order sample and on-site edits immediately update details without repricing',()=>{
  const h=harness();
  h.frappe.ngs_item_details.render(h.frm);
  assert(h.html.includes('Sample: DNA'));
  h.row.sample_type='Blood';
  h.handlers['NGS Order Item'].sample_type(h.frm);
  assert(h.html.includes('Sample: Blood'));
  assert(!h.html.includes('Sample: DNA'));
  h.row.onsite_service=1;
  h.row.onsite_address='Test Address';
  h.handlers['NGS Order Item'].onsite_service(h.frm);
  assert(h.html.includes('On-site Service: Yes'));
  assert(h.html.includes('Test Address'));
  assert.equal(h.row.unit_price,576);
  assert.equal(h.row.amount,576);
});

test('all displayed Order fields have render triggers',()=>{
  const h=harness();
  for(const field of ['items_remove','project_type','service','sample_type','species','read_depth',
    'million_reads_per_sample','data_analysis','description','notes','onsite_service','onsite_location',
    'onsite_address','library_qc','tissue_dissociation','nuclei_extraction','add_on_sequencing',
    'confirmed_tbd_fee','use_price_override','override_unit_price','unit_price','amount']) {
    assert.equal(typeof h.handlers['NGS Order Item'][field],'function',field);
    h.handlers['NGS Order Item'][field](h.frm);
    assert(h.html.includes('WGS - 45x'),field);
  }
});

test('quantity trigger waits for amount update before rendering the new total',async()=>{
  const h=harness();
  h.row.quantity=2;
  await h.handlers['NGS Order Item'].quantity(h.frm,'NGS Order Item',h.row.name);
  assert.equal(h.row.amount,1152);
  assert(h.html.includes('$1152.00'));
  assert(h.html.includes('WGS - 45x'));
});
