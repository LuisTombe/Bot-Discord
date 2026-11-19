import unittest
from simple_flow_ui import AuthorizationModal
from simple_market_ui import ProfileModal, PurchaseModal
from wx_market.ui import DecisionModal

class FormLimits(unittest.IsolatedAsyncioTestCase):
    async def test_serialized_modal_labels(self):
        modals=[AuthorizationModal(None,{'data':{}}),ProfileModal(None,{},'humano'),PurchaseModal(None,'test',5000),DecisionModal(None,1,True,2)]
        def check(node):
            if isinstance(node,dict):
                if isinstance(node.get('label'),str):self.assertTrue(1<=len(node['label'])<=45,node['label'])
                for val in node.values():check(val)
            elif isinstance(node,list):
                for val in node:check(val)
        for modal in modals:
            check(modal.to_dict());modal.stop()
