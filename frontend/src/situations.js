// The same situations the evaluation suite runs, phrased the way a buyer
// would describe them rather than by scenario id.

export const SITUATIONS = [
  {
    id: "E1",
    who: "Arequipe spread, Chapinero",
    what: "Planner wants 800 units",
    tests: "Recommendation is wrong",
    request: {
      sku: "SKU-ARE-001",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 800,
    },
  },
  {
    id: "E2",
    who: "Water 6-pack, Chapinero",
    what: "Planner wants 300 units",
    tests: "Need falls below the supplier minimum",
    request: {
      sku: "SKU-AGU-002",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 300,
    },
  },
  {
    id: "E3",
    who: "Ground coffee, El Poblado",
    what: "Planner wants 1,000 units",
    tests: "Budget runs out first",
    request: {
      sku: "SKU-CAF-003",
      node_id: "TURBO-MDE-02",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 1000,
    },
  },
  {
    id: "E4",
    who: "Water 6-pack, El Poblado",
    what: "Planner wants 2,000 units",
    tests: "Storage runs out first",
    request: {
      sku: "SKU-AGU-002",
      node_id: "TURBO-MDE-02",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 2000,
    },
  },
  {
    id: "E5",
    who: "Snack multipack, Chapinero",
    what: "Planner wants 200 units",
    tests: "Sales are running 2.6x forecast",
    request: {
      sku: "SKU-SNK-005",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 200,
    },
  },
  {
    id: "E6",
    who: "Whole milk, Chapinero",
    what: "Planner wants 1,500 units",
    tests: "Shelf life caps how much is sensible",
    request: {
      sku: "SKU-LEC-004",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 1500,
    },
  },
  {
    id: "E7",
    who: "Ground coffee, Chapinero",
    what: "Planner wants 550 units",
    tests: "Correct, and small enough to act alone",
    request: {
      sku: "SKU-CAF-003",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 550,
    },
  },
  {
    id: "E8",
    who: "Arequipe spread, Pinheiros",
    what: "Planner wants 600 units",
    tests: "Nothing legal can be bought",
    request: {
      sku: "SKU-ARE-001",
      node_id: "TURBO-SAO-03",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 600,
    },
  },
  {
    id: "E9",
    who: "Ground coffee, Chapinero",
    what: "Supplier confirms only half",
    tests: "Outcome differs from intent",
    request: {
      sku: "SKU-CAF-003",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 550,
      failure_profile: {
        "SUP-ANDINA": {
          fill_rate: 0.5,
          round_to: 50,
          message: "Roaster capacity shortfall this cycle.",
        },
      },
    },
  },
  {
    id: "E10",
    who: "Ground coffee, Chapinero",
    what: "Supplier rejects outright",
    tests: "Nothing arrives at all",
    request: {
      sku: "SKU-CAF-003",
      node_id: "TURBO-BOG-01",
      supplier_id: "SUP-ANDINA",
      recommended_quantity: 550,
      failure_profile: {
        "SUP-ANDINA": {
          fill_rate: 0,
          message: "SKU discontinued at this supplier.",
        },
      },
    },
  },
];
