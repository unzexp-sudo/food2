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
    dashboard: {
      needsAttention: "待处理事项",
      waitingReview: "{{count}} 个订单等待审核",
      waitingConfirm: "{{count}} 个订单等待确认",
      nothingWaiting: "暂无待处理事项 — 所有订单均已核对。",
      parkedInfo: "{{count}} 条消息被判定为非订单，已从收件箱隐藏",
      reviewParked: "查看",
    },
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
      // 第三道关口：订单已生成，但尚未经人工确认。
      awaitingBanner: "{{count}} 个订单等待确认",
      awaitingHint: "加粗的行需要人工确认。未经确认的订单不会发送，也不会进入后续处理。",
      awaitingConfirmTitle: "此订单尚未确认",
      awaitingConfirmBody: "此后不会自动进行任何处理。请核对下方明细，确认后订单才会流转。",
      awaitingConfirmNoPermission: "此后不会自动进行任何处理。需要具有运营或管理员权限的人员进行确认。",
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
    intake: {
      pendingBanner: "{{count}} 个订单等待人工审核",
      pendingOnly: "只看待审核",
      showAll: "显示全部",
      unreviewed: "待审核",
      waitingSince: "自 {{time}} 起等待",
      reviewHint: "加粗的行仍需人工核对，核对后才会生成订单。",
      parkedBanner: "{{count}} 条消息被判定为非订单并已归档",
      parkedOnly: "只看已归档",
      parkedHint: "这些消息疑似闲聊而非订单，默认不在收件箱显示。如判断有误，请点「恢复」放回处理队列。",
      promote: "恢复",
      promoteConfirm: "将该消息放回待处理队列？",
      promoteSuccess: "已放回处理队列",
      review: {
        verifyTitle: "生成订单前请核对",
        verifyBody: "订单尚未生成。请对照原件核对下方明细，确认后再提交。",
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
