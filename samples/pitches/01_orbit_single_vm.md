# Orbit Retail - Black Friday storefront (expected: I'm out)

Orbit Retail wants their Black Friday storefront on a single Compute Engine VM in us-central1-a
running a self-managed MySQL database on the same VM. The firewall allows SSH on port 22 from
0.0.0.0/0 and the site is served over plain HTTP. There is an admin back-office page on the same
host. Data is internal (catalogue + orders, no card data - payments go to a third-party SaaS).
Target SLO 99.9%. Budget $1,500/month. Traffic peaks at 20x normal on Black Friday.
