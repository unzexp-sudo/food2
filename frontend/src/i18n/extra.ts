/**
 * Extra translation keys contributed by the 9 parallel module agents
 * (Guanmai MVP feature polish). Kept in a separate file so module work never
 * has to edit the large shared en.ts/zh.ts. Merged into the base resources
 * in ./index.tsx via deepMerge.
 */
const extra = {
  common: {
    view: "View",
    adjust: "Adjust",
    generate: "Generate",
    preview: "Preview",
  },
  pages: {
    dashboard: {
      needsAttention: "Needs your attention",
      waitingReview: "{{count}} order(s) waiting for review",
      waitingConfirm: "{{count}} order(s) waiting for confirmation",
      nothingWaiting: "Nothing is waiting — every order has been checked.",
      // Not "needs attention" — parked messages need no action, they just
      // must not be invisible.
      parkedInfo: "{{count}} message(s) parked as non-orders and hidden from the inbox",
      reviewParked: "Check them",
    },
    sales: {
      quotationPreview: {
        product: "Product",
        qty: "Qty",
        unit: "Unit",
        unitPrice: "Unit Price",
        amount: "Amount",
        serviceTime: "Service Time",
        pricingCycle: "Pricing Cycle",
      },
    },
    master: {
      customers: {
        orderHistory: "Order history",
      },
      products: {
        viewProduct: "View Product",
        notFound: "Product not found",
      },
      wholesalers: {
        productsSupplied: "Products supplied",
        noProductsLinked: "No products linked.",
        cost: "Cost",
      },
    },
    purchaseOrders: {
      poReceive: {
        receiptRecorded: "Receipt recorded",
        receiveNow: "Receive now",
        recordGoodsReceipt: "Record goods receipt",
        poNumber: "PO {{po_number}}",
        noLines: "This purchase order has no lines.",
      },
    },
    orders: {
      // Gate 3: the order exists, but nobody has confirmed it yet.
      awaitingBanner: "{{count}} order(s) waiting for confirmation",
      awaitingHint:
        "Bold rows need a person to confirm them. No order is sent or processed until then.",
      awaitingConfirmTitle: "This order is not confirmed yet",
      awaitingConfirmBody:
        "Nothing happens automatically from here. Check the lines below, then confirm to send this order on.",
      awaitingConfirmNoPermission:
        "Nothing happens automatically from here. Someone with ops or admin rights has to confirm it.",
      orderTimeline: {
        draft: "Order created",
        pendingConfirmation: "Pending confirmation",
        confirmed: "Confirmed",
        consolidated: "Consolidated",
        fulfilled: "Fulfilled",
        invoiced: "Invoiced",
        needsClarification: "Needs clarification",
      },
    },
    intake: {
      // The "unread" queue: orders parked until a human confirms them.
      pendingBanner: "{{count}} order(s) waiting for review",
      pendingOnly: "Pending only",
      showAll: "Show all",
      unreviewed: "Unreviewed",
      waitingSince: "Waiting since {{time}}",
      reviewHint: "Bold rows still need a person to check them before an order is created.",
      // Parked messages (greetings, "收到", stickers) are hidden from the
      // inbox by default. They must stay reachable: a wrongly-parked message
      // is a lost order, so surface the count and offer a way back.
      parkedBanner: "{{count}} message(s) parked as non-orders",
      parkedOnly: "Parked only",
      parkedHint:
        "These looked like chatter, not orders. If one is actually an order, promote it back into the queue.",
      promote: "Promote",
      promoteConfirm: "Put this message back in the intake queue?",
      promoteSuccess: "Message promoted back to the queue",
      review: {
        // Every order now stops for review — not just the low-confidence ones —
        // so the drawer must not claim the parse was uncertain when it wasn't.
        verifyTitle: "Check this before it becomes an order",
        verifyBody:
          "No order exists yet. Compare the lines below against the original, then confirm.",
      },
    },
    warehouse: {
      inventory: {
        stockAdjust: {
          damage: "Damage",
          count: "Count",
          transfer: "Transfer",
          reason: "Reason",
          quantity: "Quantity",
          selectReason: "Select reason",
          reasonRequired: "Reason is required",
          quantityRequired: "Quantity is required",
          success: "Stock adjusted",
        },
      },
    },
    delivery: {
      delivery: {
        routeZone: "Route / Zone",
        driver: "Assigned Driver",
      },
    },
    finance: {
      statements: {
        title: "Generate Statement",
        hint: "Generate a customer statement for the selected date range.",
        customer: "Customer",
        validationCustomer: "Please select a customer",
        validationFrom: "Please select a start date",
        to: "To",
        validationTo: "Please select an end date",
        generated: "Statement generated",
      },
    },
  },
};

export default extra;
