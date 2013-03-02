<?php header ("Content-Type:text/xml");?>
<?xml version="1.0" encoding="UTF-8"?>
<!-- DNS Peering Protocol XML Output from dper.php PowerDNS dperexport module (c) Rickard Dahlstrand, Tilde 2013 -->

<dper>
	<peer name="ns.example.se">
		<primary>xx.xx.xx.xx</primary>

<?php

date_default_timezone_set('Europe/Stockholm');
setlocale("LC_ALL","sv_SE");

mysql_connect('localhost', 'USERNAME', 'PASSWORD');
mysql_select_db('powerdns');

$result = 0;

$query = "select name from domains;";
$dbresult = mysql_query($query);
while ($row = mysql_fetch_assoc($dbresult)) {
	echo "		<zone>". $row['name'] . "</zone>\n";
}

?>
	</peer>
</dper>

