TODO:

* Can we model EvChargeControl as a more primative ConnectionPolicy, and adapt our inputs to it? 
* Instead of having _passthrough_constraints as part of the base ConnectionPolicy class, it should be defined 
  within the passthrough policy class, and anything that needs passthrough semantics, should inherit from it.
* component_type @property on base_load (and others?) seems redundant. We could just have a static class property for this? 

* a_node_id: NodeId | str, - why are these a union? they should always use NodeId.