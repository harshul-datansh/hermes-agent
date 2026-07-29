Add grounded document search to the Datansh client portal.

Clients upload contracts, statements of work, and reports to the portal. Today they can only browse by filename, which does not scale past a few hundred documents. We want a client to ask a question in plain language and get a short answer with citations back to the exact source documents, restricted to the documents their own organization is allowed to see.

The portal is a Next.js App Router frontend talking to a Spring Boot service over PostgreSQL. Assume this is a real production feature for paying clients, not a prototype.

Produce a practical one-day implementation plan and a next-week roadmap covering the backend service, the frontend surface, and the retrieval/model layer, including how we will know the answer quality is good enough to ship.
