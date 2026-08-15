"""Deployment-readiness tooling for provisioning Genie's Azure infrastructure.

Separate from ``app.validation`` (the FastAPI startup validators): this
package checks whether an Azure *subscription* is ready to have Genie's
infrastructure provisioned onto it, before that infrastructure - and
therefore the running application - exists.
"""
