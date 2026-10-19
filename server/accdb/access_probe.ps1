param([string]$DbPath)
[Console]::OutputEncoding=[Text.Encoding]::UTF8
function CV($v){
 if($null -eq $v){ return $null }
 if($v -is [DBNull]){ return $null }
 if($v -is [DateTime]){ return $v.ToString("yyyy-MM-dd HH:mm:ss") }
 if($v -is [byte[]]){ return "[BINLEN="+$v.Length+"]" }
 return $v
}
$dbe=New-Object -ComObject DAO.DBEngine.120
$db=$dbe.OpenDatabase($DbPath)
$tables=[ordered]@{}
foreach($td in $db.TableDefs){
  if($td.Name -like "MSys*"){ continue }
  $fields=@()
  foreach($f in $td.Fields){
    $fh=[ordered]@{ name=$f.Name; type=$f.Type }
    foreach($pn in @("DefaultValue","Required","AllowZeroLength","FieldSize","InputMask","Format","DecimalPlaces","RowSourceType","RowSource","ValidationRule","ValidationText","Caption","Description","ColumnHidden")){
      try{
        $v=$f.Properties($pn).Value
        if($null -ne $v -and "$v" -ne ""){ $fh[$pn]=CV $v }
      }catch{}
    }
    $fields+=$fh
  }
  $primary=@(); $indexes=@()
  foreach($ix in $td.Indexes){
    $ifs=@(); foreach($jf in $ix.Fields){ $ifs+=$jf.Name }
    $indexes+=[ordered]@{ name=$ix.Name; primary=[bool]$ix.Primary; unique=[bool]$ix.Unique; fields=$ifs }
    if($ix.Primary){ foreach($jf in $ix.Fields){ $primary+=$jf.Name } }
  }
  # records
  $rows=@()
  try{
    $rs=$db.OpenRecordset($td.Name)
    while(-not $rs.EOF){
      $r=[ordered]@{}
      for($i=0;$i -lt $rs.Fields.Count;$i++){ $r[$rs.Fields($i).Name]=CV $rs.Fields($i).Value }
      $rows+=$r; $rs.MoveNext()
    }
    $rs.Close()
  }catch{}
  $tables[$td.Name]=[ordered]@{ fields=$fields; primary=$primary; indexes=$indexes; rows=$rows }
}
$relations=@()
foreach($rel in $db.Relations){
  $rfs=@()
  foreach($rf in $rel.Fields){ $rfs+=[ordered]@{ field=$rf.Name; foreign=$rf.ForeignName } }
  $ri=[bool]($rel.Attributes -band 4096)
  $relations+=[ordered]@{ name=$rel.Name; table=$rel.Table; foreignTable=$rel.ForeignTable; fields=$rfs; referentialIntegrity=$ri }
}
$queries=[ordered]@{}
foreach($q in $db.QueryDefs){
  if($q.Name -notlike "~*"){ $queries[$q.Name]=$q.SQL }
}
$oForms=@();$oReports=@();$oMacros=@()
try{
 $rs=$db.OpenRecordset("SELECT Name,Type FROM MSysObjects WHERE Flags=0 AND Type In(-32768,-32764,-32766)")
 while(-not $rs.EOF){
   $ot=$rs.Fields("Type").Value; $on=$rs.Fields("Name").Value
   if($ot -eq -32768){$oForms+=$on}elseif($ot -eq -32764){$oReports+=$on}elseif($ot -eq -32766){$oMacros+=$on}
   $rs.MoveNext()
 }
 $rs.Close()
}catch{}
$db.Close()
$result=[ordered]@{ tables=$tables; relations=$relations; queries=$queries; objects=[ordered]@{ forms=$oForms; reports=$oReports; macros=$oMacros } }
[Console]::Out.Write(($result | ConvertTo-Json -Depth 12 -Compress))
