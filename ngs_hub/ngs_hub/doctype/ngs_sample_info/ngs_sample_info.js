// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("NGS Sample Info", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('NGS Sample Info', {
  onload(frm) {
    // 判断是否客户或内部员工
    const roles = frappe.session.user_roles || [];
    const is_external = roles.includes('NGS External Customer');
    // 客户看不到 inside_section
    if (is_external) {
      frm.toggle_display('inside_section', false);
    }
  },
  refresh(frm) {
    // 1. 先清空，防止重复
    frm.clear_custom_buttons();
    const type = frm.doc.class_type;
    if (!type) return;
    // 2. Related Processes
    setTimeout(() => {
      const type = frm.doc.class_type;
      // hide all of them first
      $("[data-doctype='NGS SingleCell 10x Preprocess']").parent().hide();
      $("[data-doctype='NGS SingleCell Library Construction']").parent().hide();
      $("[data-doctype='NGS SingleCell Sequencing Outsource']").parent().hide();
      $("[data-doctype='NGS RNAseq Sample Extraction']").parent().hide();
      $("[data-doctype='NGS RNAseq Library Construction']").parent().hide();
      $("[data-doctype='NGS RNAseq Sequencing Outsource']").parent().hide();
      $("[data-doctype='NGS DNAseq Sample Extraction']").parent().hide();
      $("[data-doctype='NGS DNAseq Library Construction']").parent().hide();
      $("[data-doctype='NGS DNAseq Sequencing Outsource']").parent().hide();
      $("[data-doctype='NGS Meta Sample Extraction']").parent().hide();
      $("[data-doctype='NGS Meta Library Construction']").parent().hide();
      $("[data-doctype='NGS Meta Sequencing Outsource']").parent().hide();
      // show the one that matches
      if (type === 'SingleCell') {
        $("[data-doctype='NGS SingleCell 10x Preprocess']").parent().show();
        $("[data-doctype='NGS SingleCell Library Construction']").parent().show();
        $("[data-doctype='NGS SingleCell Sequencing Outsource']").parent().show();
      }
      if (type === 'RNAseq') {
        $("[data-doctype='NGS RNAseq Sample Extraction']").parent().show();
        $("[data-doctype='NGS RNAseq Library Construction']").parent().show();
        $("[data-doctype='NGS RNAseq Sequencing Outsource']").parent().show();
      }
      if (type === 'DNAseq') {
        $("[data-doctype='NGS DNAseq Sample Extraction']").parent().show();
        $("[data-doctype='NGS DNAseq Library Construction']").parent().show();
        $("[data-doctype='NGS DNAseq Sequencing Outsource']").parent().show();
      }
      if (type === 'Meta') {
        $("[data-doctype='NGS Meta Sample Extraction']").parent().show();
        $("[data-doctype='NGS Meta Library Construction']").parent().show();
        $("[data-doctype='NGS Meta Sequencing Outsource']").parent().show();
      }
    }, 100);
    // 3. 通用：Sample Transfer 跳转
    frm.add_custom_button(__('→ Sample Transfer'), () => {
      if (frm.doc.sample_transfer) {
        frappe.set_route('Form', 'NGS Sample Transfer', frm.doc.sample_transfer);
      } else {
        frappe.msgprint(__('No associated Sample Transfer found.'));
      }
    });
    // 4. 根据 class_type 分发
    if (type === 'SingleCell') {
      add_singlecell_buttons(frm);
    } else if (type === 'RNAseq') {
      add_rnaseq_buttons(frm);
    } else if (type === 'DNAseq') {
      add_dnaseq_buttons(frm);
    } else if (type === 'Meta') {
      add_meta_buttons(frm);
    }
  }
});

/* ============================= */
/*  各类型按钮定义               */
/* ============================= */

function add_singlecell_buttons(frm) {
  frm.add_custom_button(__('+ Pre'), () => {
    create_doc(frm, 'NGS SingleCell 10x Preprocess');
  });
  frm.add_custom_button(__('+ Lib'), () => {
    create_doc(frm, 'NGS SingleCell Library Construction');
  });
  frm.add_custom_button(__('+ Seq'), () => {
    create_doc(frm, 'NGS SingleCell Sequencing Outsource');
  });
}

function add_rnaseq_buttons(frm) {
  frm.add_custom_button(__('+ Ext'), () => {
    create_doc(frm, 'NGS RNAseq Sample Extraction');
  });
  frm.add_custom_button(__('+ Lib'), () => {
    create_doc(frm, 'NGS RNAseq Library Construction');
  });
  frm.add_custom_button(__('+ Seq'), () => {
    create_doc(frm, 'NGS RNAseq Sequencing Outsource');
  });
}

function add_dnaseq_buttons(frm) {
  frm.add_custom_button(__('+ Ext'), () => {
    create_doc(frm, 'NGS DNAseq Sample Extraction');
  });
  frm.add_custom_button(__('+ Lib'), () => {
    create_doc(frm, 'NGS DNAseq Library Construction');
  });
  frm.add_custom_button(__('+ Seq'), () => {
    create_doc(frm, 'NGS DNAseq Sequencing Outsource');
  });
}

function add_meta_buttons(frm) {
  frm.add_custom_button(__('+ Ext'), () => {
    create_doc(frm, 'NGS Meta Sample Extraction');
  });
  frm.add_custom_button(__('+ Lib'), () => {
    create_doc(frm, 'NGS Meta Library Construction');
  });
  frm.add_custom_button(__('+ Seq'), () => {
    create_doc(frm, 'NGS Meta Sequencing Outsource');
  });
}

/* ============================= */
/*  通用创建函数（核心复用）      */
/* ============================= */

function create_doc(frm, doctype) {
  frappe.new_doc(doctype, {}, (doc) => {
    doc.customer = frm.doc.customer;
    doc.project = frm.doc.project;
    doc.sample_transfer = frm.doc.sample_transfer;
    doc.sample_info = frm.doc.sample_info_id;
    doc.urgency = frm.doc.urgency;
  });
}
