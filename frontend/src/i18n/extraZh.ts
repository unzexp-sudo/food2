/**
 * 中文补充翻译键（与 ./extra.ts 结构一致），由 9 个并行模块 agent 贡献。
 * 在 ./index.tsx 中通过 deepMerge 合并进基础资源，模块代码无需改动 en.ts/zh.ts。
 */
const extraZh = {
  common: {
    view: "查看",
    adjust: "调整",
    generate: "生成",
    preview: "预览",
  },
  pages: {
    sales: {
      quotationPreview: {
        product: "商品",
        qty: "数量",
        unit: "单位",
        unitPrice: "单价",
        amount: "金额",
        serviceTime: "服务时间",
        pricingCycle: "计价周期",
      },
    },
    master: {
      customers: {
        orderHistory: "订单历史",
      },
      products: {
        viewProduct: "查看商品",
        notFound: "未找到商品",
      },
      wholesalers: {
        productsSupplied: "供应商品",
        noProductsLinked: "暂无关联商品。",
        cost: "成本",
      },
    },
    purchaseOrders: {
      poReceive: {
        receiptRecorded: "已记录收货",
        receiveNow: "立即收货",
        recordGoodsReceipt: "记录收货",
        poNumber: "采购单 {{po_number}}",
        noLines: "该采购单没有明细行。",
      },
    },
    orders: {
      orderTimeline: {
        draft: "订单创建",
        pendingConfirmation: "待确认",
        confirmed: "已确认",
        consolidated: "已合并",
        fulfilled: "已履约",
        invoiced: "已开票",
        needsClarification: "需澄清",
      },
    },
    warehouse: {
      inventory: {
        stockAdjust: {
          damage: "损耗",
          count: "盘点",
          transfer: "转储",
          reason: "原因",
          quantity: "数量",
          selectReason: "选择原因",
          reasonRequired: "原因为必填项",
          quantityRequired: "数量为必填项",
          success: "库存已调整",
        },
      },
    },
    delivery: {
      delivery: {
        routeZone: "路线 / 区域",
        driver: "指派司机",
      },
    },
    finance: {
      statements: {
        title: "生成对账单",
        hint: "为所选日期范围生成客户对账单。",
        customer: "客户",
        validationCustomer: "请选择客户",
        validationFrom: "请选择开始日期",
        to: "至",
        validationTo: "请选择结束日期",
        generated: "对账单已生成",
      },
    },
  },
};

export default extraZh;
