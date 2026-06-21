"""FastAPI backend for the jobsearch product.

A thin HTTP layer over the ``jobsearch`` core. Auth is delegated to Supabase;
data access goes through the core's SupabaseJobStore / SupabaseUserState seams
with the backend's service_role key. Business endpoints are added in later steps;
this package currently exposes only /health and /me.
"""
