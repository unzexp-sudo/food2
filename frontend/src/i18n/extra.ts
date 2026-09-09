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
